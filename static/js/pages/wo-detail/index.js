// ============================================================
// WO Detail page — orchestration (Phase 5f split).
// Flask route /work-orders/<wo_id>. Server-renders the initial
// HTML; this module:
//   - polls /api/work-orders/<wo_id> every 2500ms
//   - owns the shared state at window.WoDetail._state
//   - dispatches to renderers (detail-render.js) and handlers
//     (detail-actions.js) which live in sibling files
//   - defers DOM rewrites while a modal is open
// Render + action functions are defined in the sibling scripts;
// this file just wires up the lifecycle.
// ============================================================

(function () {
    var WO_DETAIL_POLL_MS = 2500;

    var W = (window.WoDetail = window.WoDetail || {});
    W._state = W._state || {
        pollTimer: null,
        pollInflight: false,
        woId: null,
        selectedQueueIds: {},
        expandedJobs: {},
        collapsedJobs: {},
        lastSnapshot: null,
    };
    var S = W._state;

    function init() {
        var section = document.getElementById('page-wo-detail');
        if (!section) return;
        S.woId = section.getAttribute('data-wo-id');
        if (!S.woId) return;

        // Seed expanded state from the server-rendered DOM so user-driven
        // expands during the first poll round survive.
        document.querySelectorAll('.job-card').forEach(function (card) {
            var jid = card.getAttribute('data-job-id');
            if (!jid) return;
            if (card.classList.contains('job-card-expanded')) {
                S.expandedJobs[jid] = true;
            }
        });

        if (window.WO_DETAIL_INITIAL) {
            S.lastSnapshot = window.WO_DETAIL_INITIAL;
            if (W._render && W._render.rightRail) {
                W._render.rightRail(window.WO_DETAIL_INITIAL);
            }
        }

        startPoll();
    }

    function startPoll() {
        if (S.pollTimer) return;
        // First tick fires after one interval — the page was just
        // server-rendered, no need to immediately re-hit the endpoint.
        S.pollTimer = setInterval(poll, WO_DETAIL_POLL_MS);
    }

    function stopPoll() {
        if (S.pollTimer) {
            clearInterval(S.pollTimer);
            S.pollTimer = null;
        }
    }

    function isModalOpen() {
        return document.querySelector('.modal-overlay.show') !== null;
    }

    async function poll() {
        if (S.pollInflight) return;
        if (isModalOpen()) return;  // defer — don't yank DOM out
        S.pollInflight = true;
        try {
            var payload = await apiGet('/api/work-orders/' + encodeURIComponent(S.woId));
            S.lastSnapshot = payload;
            if (W._render) {
                if (W._render.main) W._render.main(payload);
                if (W._render.rightRail) W._render.rightRail(payload);
            }
        } catch (e) {
            console.error('WO Detail poll failed:', e);
        } finally {
            S.pollInflight = false;
        }
    }

    // ------------------------------------------------------------
    // Public lifecycle surface
    // ------------------------------------------------------------

    W.start = function (id) { S.woId = id; init(); startPoll(); };
    W.stop = stopPoll;
    W.poll = poll;

    // Boot when the page is ready.
    if (document.readyState === 'loading') {
        document.addEventListener('DOMContentLoaded', init);
    } else {
        init();
    }
})();
