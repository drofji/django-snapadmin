// snapadmin/static/snapadmin/js/offline.js
//
// Client-side offline support for SnapModels that declare `offline_mode = True`.
// This file is injected into the admin Media only for those models, so its mere
// presence on the page means offline mode is active for the current model.
//
// Responsibilities:
//   1. Persist the current admin list view (rows scraped from the changelist
//      table) into IndexedDB, keyed by the model.
//   2. When the browser is offline, show a red banner and repaint the list from
//      the cached snapshot so the page is still usable.
//   3. Queue mutations made while offline and replay them once the connection
//      is restored, then refresh the cache from the server.

(function () {
    "use strict";

    var DB_NAME = "snapadmin_offline";
    var DB_VERSION = 1;
    var ROWS_STORE = "rows";
    var QUEUE_STORE = "queue";
    // Shared with connectivity.js so the sidebar can badge offline-capable models
    // even while the browser is offline (no network fetch possible).
    var LEARNED_KEY = "snapadmin:offline-models";

    // The presence of this script means the current model is offline-capable.
    // connectivity.js reads this flag to choose the reassuring banner (here) over
    // the "changes won't be saved" warning, and to skip the form-submit guard.
    window.SNAPADMIN_OFFLINE_CAPABLE = true;

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

    // ---- Model identity -----------------------------------------------------
    // Derive "app_label/model_name" from the admin URL: /admin/<app>/<model>/...
    function getModelKey() {
        var match = window.location.pathname.match(/\/admin\/([^/]+)\/([^/]+)\//);
        if (!match) return null;
        return match[1] + "/" + match[2];
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
    function cacheRows(modelKey, rows) {
        return openDB().then(function (db) {
            return new Promise(function (resolve, reject) {
                var req = tx(db, ROWS_STORE, "readwrite").put({
                    model: modelKey,
                    rows: rows,
                    cachedAt: Date.now()
                });
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

    // ---- Offline banner -----------------------------------------------------
    function showBanner() {
        if (document.getElementById("snapadmin-offline-banner")) return;
        var banner = document.createElement("div");
        banner.id = "snapadmin-offline-banner";
        banner.setAttribute("role", "alert");
        banner.textContent = "Offline mode — showing cached data. Changes will sync when you reconnect.";
        banner.style.cssText = [
            "position:sticky", "top:0", "z-index:9999", "width:100%",
            "box-sizing:border-box", "padding:10px 16px", "text-align:center",
            "font-weight:600", "color:#fff", "background:#DC2626",
            "box-shadow:0 1px 4px rgba(0,0,0,0.2)"
        ].join(";");
        document.body.insertBefore(banner, document.body.firstChild);
    }

    function hideBanner() {
        var banner = document.getElementById("snapadmin-offline-banner");
        if (banner && banner.parentNode) banner.parentNode.removeChild(banner);
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

    // ---- Sync queued mutations once back online -----------------------------
    function getCsrfToken() {
        var match = document.cookie.match(/csrftoken=([^;]+)/);
        return match ? match[1] : "";
    }

    function sync() {
        return readQueue().then(function (items) {
            if (!items.length) return Promise.resolve();
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
            return chain.then(clearQueue);
        });
    }

    // ---- Connectivity handling ----------------------------------------------
    function handleOffline() {
        var key = getModelKey();
        showBanner();
        if (!key) return;
        readRows(key).then(repaintFromCache).catch(function () {});
    }

    function handleOnline() {
        hideBanner();
        sync().then(function () {
            // Refresh cache snapshot from the now-live page on next load.
            var key = getModelKey();
            if (key) cacheRows(key, scrapeRows()).catch(function () {});
        }).catch(function () {});
    }

    // ---- Bootstrap ----------------------------------------------------------
    function init() {
        var key = getModelKey();
        rememberCapableModel(key);
        if (key && navigator.onLine) {
            // Live page: snapshot the current list for offline use.
            cacheRows(key, scrapeRows()).catch(function () {});
        }
        if (!navigator.onLine) {
            handleOffline();
        }
        window.addEventListener("offline", handleOffline);
        window.addEventListener("online", handleOnline);
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
        showBanner: showBanner,
        hideBanner: hideBanner,
        sync: sync
    };

    if (document.readyState === "loading") {
        document.addEventListener("DOMContentLoaded", init);
    } else {
        init();
    }
})();
