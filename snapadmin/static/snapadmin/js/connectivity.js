// snapadmin/static/snapadmin/js/connectivity.js
//
// Admin-wide connectivity awareness. Loaded on every SnapModel admin page.
//
// Connectivity is decided by whether the Django backend actually answers, not by
// navigator.onLine alone: a laptop can hold a Wi-Fi link while the server is down,
// the VPN dropped, or the box is unreachable. We therefore poll GET /api/health/
// (with a short timeout) and treat the backend as "up" only when it responds.
// navigator.onLine + visibility changes are used as cheap triggers to re-check
// sooner, never as the source of truth.
//
// The resolved state is published as a single `snapadmin:connectivity` DOM event
// ({up: bool}) so connectivity.js and the per-model engine (offline.js) always
// agree. Notifications are dynamic, auto-dismissing toasts (window.SnapAdminToast)
// rather than static banners.
//
// Behaviour when the backend goes down:
//   * Offline-capable models (offline_mode = True) load offline.js, which shows a
//     reassuring "cached / will sync" toast and a saved-objects panel. connectivity.js
//     only marks the body offline so sidebar badges animate.
//   * Every other model gets a warning toast ("objects can't be shown right now")
//     plus a hard guard: form submits are blocked and Save buttons disabled until
//     the backend returns. Already-rendered content is left untouched.
//   * Sidebar links are badged: green sync icon for offline-capable models (spins
//     while the backend is down), muted "no-offline" icon for the rest.

(function () {
    "use strict";

    var LEARNED_KEY = "snapadmin:offline-models";
    var LIMITS_KEY = "snapadmin:offline-limits";
    var SPECIAL = ["add", "change", "delete", "history"];
    var DEFAULT_INTERVAL = 15000;   // ms between health polls while visible
    var HIDDEN_INTERVAL = 60000;    // slower cadence when the tab is hidden
    var HEALTH_TIMEOUT = 4000;      // ms before a health probe is considered failed

    var blocked = false;
    var isBackendUp = true;         // optimistic until the first probe proves otherwise
    var probed = false;             // has at least one probe completed?
    var pollTimer = null;

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

    function writeLimits(limits) {
        if (!limits) return;
        try {
            window.localStorage.setItem(LIMITS_KEY, JSON.stringify(limits));
        } catch (e) { /* localStorage unavailable */ }
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

    // Refresh the capable-model set + cache limits from the server (best effort).
    function loadModelList() {
        return fetch(apiBase() + "offline-models/", {
            credentials: "same-origin",
            headers: { "Accept": "application/json" }
        }).then(function (r) {
            return r.ok ? r.json() : null;
        }).then(function (data) {
            if (data && data.models) {
                mergeLearned(data.models);
                writeLimits(data.limits);
                decorateSidebar();
            }
        }).catch(function () { /* backend down — fall back to learned set */ });
    }

    // ---- Styles -------------------------------------------------------------
    function injectStyles() {
        if (document.getElementById("snapadmin-connectivity-style")) return;
        var style = document.createElement("style");
        style.id = "snapadmin-connectivity-style";
        style.textContent = [
            // Toast stack (dynamic notifications).
            "#snapadmin-toasts{position:fixed;top:12px;right:12px;z-index:10000;",
            "display:flex;flex-direction:column;gap:8px;max-width:360px;pointer-events:none}",
            ".snap-toast{pointer-events:auto;box-sizing:border-box;padding:10px 14px;",
            "border-radius:8px;color:#fff;font-weight:600;font-size:13px;line-height:1.35;",
            "box-shadow:0 4px 14px rgba(0,0,0,.22);display:flex;align-items:flex-start;",
            "gap:8px;opacity:0;transform:translateY(-6px);transition:opacity .2s,transform .2s}",
            ".snap-toast.snap-toast-in{opacity:1;transform:translateY(0)}",
            ".snap-toast-info{background:#2563EB}",
            ".snap-toast-warn{background:#DC2626}",
            ".snap-toast-success{background:#059669}",
            ".snap-toast-close{margin-left:auto;cursor:pointer;opacity:.8;font-weight:700;",
            "background:none;border:0;color:inherit;font-size:15px;line-height:1;padding:0 2px}",
            // Sidebar badges.
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

    // ---- Toasts (dynamic notifications) -------------------------------------
    function toastContainer() {
        var el = document.getElementById("snapadmin-toasts");
        if (!el) {
            el = document.createElement("div");
            el.id = "snapadmin-toasts";
            el.setAttribute("aria-live", "polite");
            document.body.appendChild(el);
        }
        return el;
    }

    // Show a toast. opts: {type:'info'|'warn'|'success', duration:ms, id, sticky:bool}.
    // A toast with an `id` replaces any existing toast of that id (so a state change
    // updates the message in place instead of stacking duplicates).
    function showToast(message, opts) {
        opts = opts || {};
        injectStyles();
        var container = toastContainer();
        if (opts.id) dismissToast(opts.id);

        var toast = document.createElement("div");
        toast.className = "snap-toast snap-toast-" + (opts.type || "info");
        if (opts.id) toast.setAttribute("data-snap-toast-id", opts.id);

        var text = document.createElement("span");
        text.textContent = message;
        toast.appendChild(text);

        var close = document.createElement("button");
        close.className = "snap-toast-close";
        close.type = "button";
        close.setAttribute("aria-label", "Dismiss");
        close.innerHTML = "&times;";
        close.addEventListener("click", function () { removeToast(toast); });
        toast.appendChild(close);

        container.appendChild(toast);
        // Trigger the enter transition on the next frame.
        window.requestAnimationFrame(function () { toast.classList.add("snap-toast-in"); });

        if (!opts.sticky) {
            window.setTimeout(function () { removeToast(toast); }, opts.duration || 5000);
        }
        return toast;
    }

    function removeToast(toast) {
        if (!toast || !toast.parentNode) return;
        toast.classList.remove("snap-toast-in");
        window.setTimeout(function () {
            if (toast.parentNode) toast.parentNode.removeChild(toast);
        }, 200);
    }

    function dismissToast(id) {
        var el = document.querySelector('[data-snap-toast-id="' + id + '"]');
        if (el) removeToast(el);
    }

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

    // ---- Form guard ---------------------------------------------------------
    function submitGuard(event) {
        if (blocked) {
            event.preventDefault();
            event.stopPropagation();
            showToast("You are offline — changes on this page will NOT be saved. Reconnect before saving.",
                { type: "warn", id: "conn-blocked", sticky: true });
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

    // ---- Connectivity state -------------------------------------------------
    // Probe the backend. Resolves to true when /api/health/ answers, false otherwise.
    function checkBackend() {
        if (!navigator.onLine) return Promise.resolve(applyState(false));

        var controller = ("AbortController" in window) ? new AbortController() : null;
        var timer = controller && window.setTimeout(function () { controller.abort(); }, HEALTH_TIMEOUT);
        var opts = {
            credentials: "same-origin",
            cache: "no-store",
            headers: { "Accept": "application/json" }
        };
        if (controller) opts.signal = controller.signal;

        return fetch(apiBase() + "health/", opts).then(function (r) {
            if (timer) window.clearTimeout(timer);
            return applyState(!!r && r.ok);
        }).catch(function () {
            if (timer) window.clearTimeout(timer);
            return applyState(false);
        });
    }

    // Commit a probe result; broadcast + react only when the state actually changes.
    function applyState(up) {
        var changed = (up !== isBackendUp) || !probed;
        isBackendUp = up;
        probed = true;
        if (changed) {
            if (up) onBackendUp(); else onBackendDown();
            document.dispatchEvent(new CustomEvent("snapadmin:connectivity", { detail: { up: up } }));
        }
        return up;
    }

    function onBackendDown() {
        document.body.classList.add("snap-offline");
        if (!isCurrentCapable()) {
            showToast("Backend unreachable — objects can't be shown right now. This page is read-only until you reconnect.",
                { type: "warn", id: "conn-state", sticky: true });
            setSaveBlocked(true);
        }
        // Offline-capable pages: offline.js owns the reassuring toast + panel.
    }

    function onBackendUp() {
        document.body.classList.remove("snap-offline");
        dismissToast("conn-state");
        dismissToast("conn-blocked");
        setSaveBlocked(false);
        if (probed) {
            // Don't announce the very first "up" on a healthy page load.
            showToast("Back online — backend reachable.", { type: "success", id: "conn-state", duration: 3000 });
        }
        loadModelList();
    }

    // ---- Polling lifecycle --------------------------------------------------
    function currentInterval() {
        var base = Number(window.SNAPADMIN_HEALTH_INTERVAL) || DEFAULT_INTERVAL;
        return document.hidden ? Math.max(base, HIDDEN_INTERVAL) : base;
    }

    function scheduleNext() {
        if (pollTimer) window.clearTimeout(pollTimer);
        pollTimer = window.setTimeout(poll, currentInterval());
    }

    function poll() {
        checkBackend().then(scheduleNext, scheduleNext);
    }

    function triggerImmediateCheck() {
        if (pollTimer) window.clearTimeout(pollTimer);
        poll();
    }

    // ---- Bootstrap ----------------------------------------------------------
    function init() {
        injectStyles();
        decorateSidebar();
        // Guard listener stays installed; it is a no-op until `blocked` is true.
        guardedForms().forms.forEach(function (form) {
            form.addEventListener("submit", submitGuard, true);
        });
        // Browser connectivity changes and tab refocus are cheap triggers to
        // re-probe immediately; the poll loop is the real source of truth.
        window.addEventListener("offline", triggerImmediateCheck);
        window.addEventListener("online", triggerImmediateCheck);
        document.addEventListener("visibilitychange", function () {
            if (!document.hidden) triggerImmediateCheck();
        });
        window.addEventListener("beforeunload", function () {
            if (pollTimer) window.clearTimeout(pollTimer);
        });
        poll();
    }

    window.SnapAdminToast = {
        show: showToast,
        dismiss: dismissToast
    };

    window.SnapAdminConnectivity = {
        currentModelKey: currentModelKey,
        linkModelKey: linkModelKey,
        capableSet: capableSet,
        isCurrentCapable: isCurrentCapable,
        decorateSidebar: decorateSidebar,
        setSaveBlocked: setSaveBlocked,
        loadModelList: loadModelList,
        checkBackend: checkBackend,
        isBackendUp: function () { return isBackendUp; },
        toast: showToast
    };

    if (document.readyState === "loading") {
        document.addEventListener("DOMContentLoaded", init);
    } else {
        init();
    }
})();
