// snapadmin/static/snapadmin/js/offline.js
//
// Client-side offline support for SnapModels that declare `offline_mode = True`.
// This file is injected into the admin Media only for those models, so its mere
// presence on the page means offline mode is active for the current model.
//
// Responsibilities:
//   1. While the backend is reachable, prefetch the most-recent rows from
//      GET /api/offline-data/<app>/<model>/ (capped by the model's
//      offline_cache_limit) and persist them in IndexedDB, keyed by model. The
//      rendered changelist is kept as a fallback snapshot.
//   2. When the backend is unreachable, repaint the list from the cache and show
//      a saved-objects panel listing exactly what is cached and how many changes
//      are queued, plus a reassuring toast.
//   3. Queue mutations made while offline and replay them once the backend is
//      reachable again, then refresh the cache.
//
// Connectivity is owned by connectivity.js: it polls /api/health/ and broadcasts
// a `snapadmin:connectivity` ({up}) DOM event. offline.js reacts to that event
// rather than to navigator.onLine, so both layers agree on one state.

(function () {
    "use strict";

    var DB_NAME = "snapadmin_offline";
    var DB_VERSION = 1;
    var ROWS_STORE = "rows";
    var QUEUE_STORE = "queue";
    // Shared with connectivity.js so the sidebar can badge offline-capable models
    // even while the backend is unreachable (no network fetch possible).
    var LEARNED_KEY = "snapadmin:offline-models";
    var LIMITS_KEY = "snapadmin:offline-limits";

    // The presence of this script means the current model is offline-capable.
    // connectivity.js reads this flag to skip its warning toast + form guard here.
    window.SNAPADMIN_OFFLINE_CAPABLE = true;

    // ---- Toast bridge -------------------------------------------------------
    // connectivity.js loads first and exposes window.SnapAdminToast; fall back to
    // a no-op if it is somehow absent so offline.js never throws.
    function toast(message, opts) {
        if (window.SnapAdminToast && window.SnapAdminToast.show) {
            return window.SnapAdminToast.show(message, opts);
        }
        return null;
    }

    // Remember this model key locally so the sidebar can mark it as syncable
    // on any admin page, including offline.
    function rememberCapableModel(key) {
        if (!key) return;
        try {
            var stored = JSON.parse(window.localStorage.getItem(LEARNED_KEY) || "[]");
            if (stored.indexOf(key) === -1) {
                stored.push(key);
                window.localStorage.setItem(LEARNED_KEY, JSON.stringify(stored));
            }
        } catch (e) { /* localStorage unavailable */ }
    }

    function cacheLimitFor(key) {
        try {
            var limits = JSON.parse(window.localStorage.getItem(LIMITS_KEY) || "{}");
            return limits[key] || null;
        } catch (e) { return null; }
    }

    // ---- Model identity -----------------------------------------------------
    // Derive "app_label/model_name" from the admin URL: /admin/<app>/<model>/...
    function getModelKey() {
        var match = window.location.pathname.match(/\/admin\/([^/]+)\/([^/]+)\//);
        if (!match) return null;
        return match[1] + "/" + match[2];
    }

    function apiBase() {
        return (window.SNAPADMIN_API_BASE || "/api/").replace(/\/?$/, "/");
    }

    // ---- IndexedDB plumbing -------------------------------------------------
    function openDB() {
        return new Promise(function (resolve, reject) {
            if (!window.indexedDB) {
                reject(new Error("IndexedDB unavailable"));
                return;
            }
            var request = window.indexedDB.open(DB_NAME, DB_VERSION);
            request.onupgradeneeded = function (event) {
                var db = event.target.result;
                if (!db.objectStoreNames.contains(ROWS_STORE)) {
                    db.createObjectStore(ROWS_STORE, { keyPath: "model" });
                }
                if (!db.objectStoreNames.contains(QUEUE_STORE)) {
                    db.createObjectStore(QUEUE_STORE, { keyPath: "id", autoIncrement: true });
                }
            };
            request.onsuccess = function (event) { resolve(event.target.result); };
            request.onerror = function () { reject(request.error); };
        });
    }

    function tx(db, store, mode) {
        return db.transaction(store, mode).objectStore(store);
    }

    // ---- Public cache API ---------------------------------------------------
    // `extra` carries the structured payload (objects, fields, limit) from the
    // offline-data endpoint; the scraped rows remain the fallback snapshot.
    function cacheRows(modelKey, rows, extra) {
        return openDB().then(function (db) {
            return new Promise(function (resolve, reject) {
                var record = {
                    model: modelKey,
                    rows: rows,
                    cachedAt: Date.now()
                };
                if (extra) {
                    record.objects = extra.objects || null;
                    record.fields = extra.fields || null;
                    record.limit = extra.limit || null;
                }
                var req = tx(db, ROWS_STORE, "readwrite").put(record);
                req.onsuccess = function () { resolve(); };
                req.onerror = function () { reject(req.error); };
            });
        });
    }

    function readRows(modelKey) {
        return openDB().then(function (db) {
            return new Promise(function (resolve, reject) {
                var req = tx(db, ROWS_STORE, "readonly").get(modelKey);
                req.onsuccess = function () { resolve(req.result || null); };
                req.onerror = function () { reject(req.error); };
            });
        });
    }

    function queueMutation(mutation) {
        return openDB().then(function (db) {
            return new Promise(function (resolve, reject) {
                var record = { model: getModelKey(), mutation: mutation, queuedAt: Date.now() };
                var req = tx(db, QUEUE_STORE, "readwrite").add(record);
                req.onsuccess = function () { resolve(req.result); };
                req.onerror = function () { reject(req.error); };
            });
        });
    }

    function readQueue() {
        return openDB().then(function (db) {
            return new Promise(function (resolve, reject) {
                var req = tx(db, QUEUE_STORE, "readonly").getAll();
                req.onsuccess = function () { resolve(req.result || []); };
                req.onerror = function () { reject(req.error); };
            });
        });
    }

    function clearQueue() {
        return openDB().then(function (db) {
            return new Promise(function (resolve, reject) {
                var req = tx(db, QUEUE_STORE, "readwrite").clear();
                req.onsuccess = function () { resolve(); };
                req.onerror = function () { reject(req.error); };
            });
        });
    }

    // ---- Fetch the most-recent rows from the offline-data endpoint ----------
    function fetchOfflineData(modelKey) {
        if (!modelKey) return Promise.resolve(null);
        var url = apiBase() + "offline-data/" + modelKey + "/";
        return fetch(url, {
            credentials: "same-origin",
            cache: "no-store",
            headers: { "Accept": "application/json" }
        }).then(function (r) {
            return r.ok ? r.json() : null;
        }).catch(function () { return null; });
    }

    // ---- Scrape the current changelist into plain row objects ---------------
    function scrapeRows() {
        var rows = [];
        var table = document.querySelector("#result_list, #changelist-form table");
        if (!table) return rows;
        var headers = [];
        table.querySelectorAll("thead th").forEach(function (th) {
            headers.push((th.textContent || "").trim());
        });
        table.querySelectorAll("tbody tr").forEach(function (tr) {
            var cells = tr.querySelectorAll("td, th");
            var link = tr.querySelector("a[href]");
            var record = { _href: link ? link.getAttribute("href") : null, _html: tr.innerHTML };
            cells.forEach(function (cell, idx) {
                var key = headers[idx] || ("col" + idx);
                record[key] = (cell.textContent || "").trim();
            });
            rows.push(record);
        });
        return rows;
    }

    // Snapshot the current page: prefer the structured endpoint, always keep the
    // scraped rows as a fallback so the list repaints even if the API is gone.
    function snapshot(modelKey) {
        if (!modelKey) return Promise.resolve();
        var scraped = scrapeRows();
        return fetchOfflineData(modelKey).then(function (data) {
            var extra = data ? { objects: data.objects, fields: data.fields, limit: data.limit } : null;
            return cacheRows(modelKey, scraped, extra);
        });
    }

    // ---- Saved-objects panel ------------------------------------------------
    function relativeTime(ts) {
        if (!ts) return "unknown";
        var secs = Math.round((Date.now() - ts) / 1000);
        if (secs < 60) return secs + "s ago";
        var mins = Math.round(secs / 60);
        if (mins < 60) return mins + "m ago";
        var hrs = Math.round(mins / 60);
        if (hrs < 24) return hrs + "h ago";
        return Math.round(hrs / 24) + "d ago";
    }

    function renderPanel(cached, queueCount) {
        var panel = document.getElementById("snapadmin-offline-panel");
        if (!panel) {
            panel = document.createElement("div");
            panel.id = "snapadmin-offline-panel";
            panel.style.cssText = [
                "position:fixed", "bottom:16px", "right:16px", "z-index:9998",
                "max-width:320px", "box-sizing:border-box", "background:#111827",
                "color:#f9fafb", "border-radius:10px", "padding:12px 14px",
                "box-shadow:0 6px 20px rgba(0,0,0,.3)", "font-size:13px", "line-height:1.4"
            ].join(";");
            document.body.appendChild(panel);
        }
        var objects = (cached && cached.objects) || [];
        var count = objects.length || (cached && cached.rows ? cached.rows.length : 0);
        var limit = (cached && cached.limit) || cacheLimitFor(getModelKey()) || count;
        var when = cached ? relativeTime(cached.cachedAt) : "unknown";

        var lines = [
            '<div style="font-weight:700;margin-bottom:6px;display:flex;align-items:center;gap:6px">' +
                '<span style="display:inline-block;width:8px;height:8px;border-radius:50%;background:#f59e0b"></span>' +
                'Offline — showing cached data</div>',
            '<div style="opacity:.85">Saved objects: <b>' + count + '</b> of last ' + limit + '</div>',
            '<div style="opacity:.85">Cached: ' + when + '</div>'
        ];
        if (queueCount) {
            lines.push('<div style="margin-top:6px;color:#fbbf24">Pending sync: <b>' +
                queueCount + '</b> change' + (queueCount === 1 ? "" : "s") + '</div>');
        }
        panel.innerHTML = lines.join("");
    }

    function removePanel() {
        var panel = document.getElementById("snapadmin-offline-panel");
        if (panel && panel.parentNode) panel.parentNode.removeChild(panel);
    }

    // ---- Repaint the list from cache when offline ---------------------------
    function repaintFromCache(cached) {
        if (!cached || !cached.rows || !cached.rows.length) return;
        var tbody = document.querySelector("#result_list tbody, #changelist-form table tbody");
        if (!tbody) return;
        tbody.innerHTML = "";
        cached.rows.forEach(function (record) {
            var tr = document.createElement("tr");
            tr.innerHTML = record._html || "";
            tbody.appendChild(tr);
        });
    }

    // ---- Sync queued mutations once the backend is reachable ----------------
    function getCsrfToken() {
        var match = document.cookie.match(/csrftoken=([^;]+)/);
        return match ? match[1] : "";
    }

    function sync() {
        return readQueue().then(function (items) {
            if (!items.length) return 0;
            var chain = Promise.resolve();
            items.forEach(function (item) {
                var m = item.mutation || {};
                chain = chain.then(function () {
                    return fetch(m.url, {
                        method: m.method || "POST",
                        headers: {
                            "Content-Type": "application/json",
                            "X-CSRFToken": getCsrfToken()
                        },
                        body: m.body ? JSON.stringify(m.body) : null,
                        credentials: "same-origin"
                    });
                });
            });
            return chain.then(clearQueue).then(function () { return items.length; });
        });
    }

    // ---- Connectivity handling ----------------------------------------------
    function handleOffline() {
        var key = getModelKey();
        toast("Offline mode — showing cached data. Changes will sync when you reconnect.",
            { type: "warn", id: "offline-state", sticky: true });
        if (!key) return;
        readRows(key).then(function (cached) {
            repaintFromCache(cached);
            readQueue().then(function (items) {
                renderPanel(cached, items.length);
            }).catch(function () { renderPanel(cached, 0); });
        }).catch(function () {});
    }

    function handleOnline() {
        if (window.SnapAdminToast && window.SnapAdminToast.dismiss) {
            window.SnapAdminToast.dismiss("offline-state");
        }
        removePanel();
        sync().then(function (n) {
            if (n) {
                toast("Synced " + n + " change" + (n === 1 ? "" : "s") + " to the server.",
                    { type: "success", duration: 4000 });
            }
            // Refresh the cache snapshot from the now-live page.
            var key = getModelKey();
            if (key) snapshot(key).catch(function () {});
        }).catch(function () {});
    }

    // ---- Bootstrap ----------------------------------------------------------
    function init() {
        var key = getModelKey();
        rememberCapableModel(key);

        // React to the shared connectivity state rather than navigator.onLine.
        document.addEventListener("snapadmin:connectivity", function (e) {
            if (e.detail && e.detail.up) handleOnline(); else handleOffline();
        });

        var backendUp = !window.SnapAdminConnectivity ||
            (window.SnapAdminConnectivity.isBackendUp && window.SnapAdminConnectivity.isBackendUp());

        if (key && backendUp) {
            // Live page: prefetch + snapshot the current list for offline use.
            snapshot(key).catch(function () {});
        }
        // If the page already loaded offline, paint from cache immediately;
        // connectivity.js will also fire the event once its first probe lands.
        if (!navigator.onLine) handleOffline();
    }

    // Expose helpers for testing and advanced usage.
    window.SnapAdminOffline = {
        getModelKey: getModelKey,
        openDB: openDB,
        cacheRows: cacheRows,
        readRows: readRows,
        queueMutation: queueMutation,
        readQueue: readQueue,
        clearQueue: clearQueue,
        scrapeRows: scrapeRows,
        fetchOfflineData: fetchOfflineData,
        snapshot: snapshot,
        renderPanel: renderPanel,
        removePanel: removePanel,
        sync: sync
    };

    if (document.readyState === "loading") {
        document.addEventListener("DOMContentLoaded", init);
    } else {
        init();
    }
})();
