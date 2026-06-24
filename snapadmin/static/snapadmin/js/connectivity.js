// snapadmin/static/snapadmin/js/connectivity.js
//
// Admin-wide connectivity awareness. Loaded on every SnapModel admin page.
//
// It complements the per-model offline engine (offline.js):
//   * Offline-capable models (offline_mode = True) load offline.js, which sets
//     window.SNAPADMIN_OFFLINE_CAPABLE and shows a reassuring "cached / will
//     sync" banner. connectivity.js stays out of the banner/guard for those.
//   * Every other model gets, when the browser goes offline, a red warning
//     banner ("changes won't be saved") plus a hard guard: form submits are
//     blocked and the Save buttons are visually disabled until the connection
//     returns. This prevents silent data loss on non-offline models.
//   * In the left sidebar, each model link is badged so the user can see at a
//     glance which models sync offline (green sync icon, spins while offline)
//     and which do not (muted "no-offline" icon, shown while offline).

(function () {
    "use strict";

    var LEARNED_KEY = "snapadmin:offline-models";
    var SPECIAL = ["add", "change", "delete", "history"];
    var blocked = false;

    // ---- Model identity helpers ---------------------------------------------
    function currentModelKey() {
        var m = window.location.pathname.match(/\/admin\/([^/]+)\/([^/]+)\//);
        return m ? m[1] + "/" + m[2] : null;
    }

    // Derive "app/model" from a changelist link, or null if it is not one.
    function linkModelKey(href) {
        if (!href) return null;
        var a = document.createElement("a");
        a.href = href;
        var parts = a.pathname.split("/").filter(Boolean);
        if (parts.length < 2) return null;
        var model = parts[parts.length - 1];
        var app = parts[parts.length - 2];
        if (SPECIAL.indexOf(model) !== -1 || /^\d+$/.test(model)) return null;
        return app + "/" + model;
    }

    // ---- Offline-capable model set ------------------------------------------
    function readLearned() {
        try {
            return JSON.parse(window.localStorage.getItem(LEARNED_KEY) || "[]");
        } catch (e) { return []; }
    }

    function writeLearned(list) {
        try {
            window.localStorage.setItem(LEARNED_KEY, JSON.stringify(list));
        } catch (e) { /* localStorage unavailable */ }
    }

    function mergeLearned(models) {
        var set = readLearned();
        var changed = false;
        (models || []).forEach(function (k) {
            if (set.indexOf(k) === -1) { set.push(k); changed = true; }
        });
        if (changed) writeLearned(set);
        return set;
    }

    function capableSet() {
        var set = readLearned();
        var current = currentModelKey();
        if (window.SNAPADMIN_OFFLINE_CAPABLE && current && set.indexOf(current) === -1) {
            set.push(current);
            writeLearned(set);
        }
        return set;
    }

    function isCurrentCapable() {
        if (window.SNAPADMIN_OFFLINE_CAPABLE) return true;
        var current = currentModelKey();
        return !!current && capableSet().indexOf(current) !== -1;
    }

    function apiBase() {
        return (window.SNAPADMIN_API_BASE || "/api/").replace(/\/?$/, "/");
    }

    // Refresh the capable-model set from the server (best effort).
    function loadModelList() {
        return fetch(apiBase() + "offline-models/", {
            credentials: "same-origin",
            headers: { "Accept": "application/json" }
        }).then(function (r) {
            return r.ok ? r.json() : null;
        }).then(function (data) {
            if (data && data.models) {
                mergeLearned(data.models);
                decorateSidebar();
            }
        }).catch(function () { /* offline or wrong base — fall back to learned set */ });
    }

    // ---- Styles -------------------------------------------------------------
    function injectStyles() {
        if (document.getElementById("snapadmin-connectivity-style")) return;
        var style = document.createElement("style");
        style.id = "snapadmin-connectivity-style";
        style.textContent = [
            "#snapadmin-conn-banner{position:sticky;top:0;z-index:9999;width:100%;",
            "box-sizing:border-box;padding:10px 16px;text-align:center;font-weight:600;",
            "color:#fff;background:#DC2626;box-shadow:0 1px 4px rgba(0,0,0,.2)}",
            ".snap-conn-badge{display:inline-flex;align-items:center;margin-left:6px;",
            "vertical-align:middle;width:14px;height:14px}",
            ".snap-conn-badge svg{width:14px;height:14px}",
            ".snap-sync-badge{color:#10b981}",
            ".snap-nooffline-badge{color:#9ca3af;display:none}",
            "body.snap-offline .snap-nooffline-badge{display:inline-flex}",
            "body.snap-offline .snap-sync-badge svg{animation:snap-spin 1s linear infinite}",
            ".snap-save-disabled{opacity:.5;pointer-events:none;cursor:not-allowed}",
            "@keyframes snap-spin{to{transform:rotate(360deg)}}"
        ].join("");
        document.head.appendChild(style);
    }

    var SYNC_SVG = '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.4" stroke-linecap="round" stroke-linejoin="round"><polyline points="23 4 23 10 17 10"/><polyline points="1 20 1 14 7 14"/><path d="M3.51 9a9 9 0 0 1 14.85-3.36L23 10M1 14l4.64 4.36A9 9 0 0 0 20.49 15"/></svg>';
    var NOOFF_SVG = '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.4" stroke-linecap="round" stroke-linejoin="round"><line x1="1" y1="1" x2="23" y2="23"/><path d="M16.72 11.06A6.5 6.5 0 0 1 18 16.5a4.5 4.5 0 0 1-1.55 3.4"/><path d="M5 19a4.5 4.5 0 0 1-1.6-8.7 8 8 0 0 1 4-5.2"/></svg>';

    // ---- Sidebar badges -----------------------------------------------------
    function makeBadge(cls, svg, title) {
        var span = document.createElement("span");
        span.className = "snap-conn-badge " + cls;
        span.innerHTML = svg;
        span.title = title;
        return span;
    }

    function decorateSidebar() {
        var capable = capableSet();
        var anchors = document.querySelectorAll(
            "#nav-sidebar a[href], .sidebar a[href], nav a[href]"
        );
        anchors.forEach(function (a) {
            if (a.querySelector(".snap-conn-badge")) return; // already decorated
            var key = linkModelKey(a.getAttribute("href"));
            if (!key) return;
            if (capable.indexOf(key) !== -1) {
                a.appendChild(makeBadge("snap-sync-badge", SYNC_SVG,
                    "Syncs offline — changes are cached and sent on reconnect"));
            } else {
                a.appendChild(makeBadge("snap-nooffline-badge", NOOFF_SVG,
                    "No offline support — changes are not saved without a connection"));
            }
        });
    }

    // ---- Warning banner (non-capable pages) ---------------------------------
    function showBanner() {
        if (document.getElementById("snapadmin-conn-banner")) return;
        var banner = document.createElement("div");
        banner.id = "snapadmin-conn-banner";
        banner.setAttribute("role", "alert");
        banner.textContent = "You are offline — changes on this page will NOT be saved. Reconnect before saving.";
        document.body.insertBefore(banner, document.body.firstChild);
    }

    function hideBanner() {
        var banner = document.getElementById("snapadmin-conn-banner");
        if (banner && banner.parentNode) banner.parentNode.removeChild(banner);
    }

    // ---- Form guard ---------------------------------------------------------
    function submitGuard(event) {
        if (blocked) {
            event.preventDefault();
            event.stopPropagation();
            showBanner();
        }
    }

    function guardedForms() {
        // Add/change forms expose a .submit-row; the changelist filter form does not.
        var rows = document.querySelectorAll(".submit-row");
        var forms = [];
        rows.forEach(function (row) {
            var form = row.closest("form");
            if (form && forms.indexOf(form) === -1) forms.push(form);
        });
        return { forms: forms, rows: rows };
    }

    function setSaveBlocked(on) {
        blocked = on;
        var g = guardedForms();
        g.rows.forEach(function (row) {
            row.classList.toggle("snap-save-disabled", on);
            row.querySelectorAll("button, input[type=submit]").forEach(function (btn) {
                btn.disabled = on;
            });
        });
    }

    // ---- Connectivity handling ----------------------------------------------
    function handleOffline() {
        document.body.classList.add("snap-offline");
        if (!isCurrentCapable()) {
            showBanner();
            setSaveBlocked(true);
        }
    }

    function handleOnline() {
        document.body.classList.remove("snap-offline");
        hideBanner();
        setSaveBlocked(false);
        loadModelList();
    }

    // ---- Bootstrap ----------------------------------------------------------
    function init() {
        injectStyles();
        decorateSidebar();
        // Guard listener stays installed; it is a no-op until `blocked` is true.
        guardedForms().forms.forEach(function (form) {
            form.addEventListener("submit", submitGuard, true);
        });
        if (!navigator.onLine) handleOffline();
        window.addEventListener("offline", handleOffline);
        window.addEventListener("online", handleOnline);
        if (navigator.onLine) loadModelList();
    }

    window.SnapAdminConnectivity = {
        currentModelKey: currentModelKey,
        linkModelKey: linkModelKey,
        capableSet: capableSet,
        isCurrentCapable: isCurrentCapable,
        decorateSidebar: decorateSidebar,
        showBanner: showBanner,
        hideBanner: hideBanner,
        setSaveBlocked: setSaveBlocked,
        loadModelList: loadModelList
    };

    if (document.readyState === "loading") {
        document.addEventListener("DOMContentLoaded", init);
    } else {
        init();
    }
})();
