// ============================================================
// Work Orders - SPA sub-tab routing (Triage / All Orders / Create)
// WO Detail is its own Flask route as of Phase 2.5c — no detail
// sub-tab here.
// ============================================================

var _woLineItemCounter = 0;
// Kept for the Print modal's pre-stuffing protocol (Triage and WO
// Detail page both populate these before opening the modal).
var _woDetailId = null;
var _woDetailQueueItems = [];
var _woDetailJobs = [];
var _woSelectedQueueIds = {};

function loadWorkOrdersPage() {
    // Default sub-tab on entering Work Orders is Triage.
    if (typeof startTriagePoll === 'function') startTriagePoll();
}

// ============================================================
// Sub-tab switching — Triage / Orders / Create
// ============================================================
function switchWoTab(tab) {
    document.querySelectorAll('[data-wotab]').forEach(function (b) {
        b.classList.remove('active');
    });
    var btn = document.querySelector('[data-wotab="' + tab + '"]');
    if (btn) btn.classList.add('active');

    var panels = ['triage', 'orders', 'create'];
    panels.forEach(function (p) {
        var el = document.getElementById('woPanel-' + p);
        if (el) {
            el.classList.remove('active');
        }
    });

    var panel = document.getElementById('woPanel-' + tab);
    if (panel) panel.classList.add('active');

    if (tab === 'triage') {
        if (typeof startTriagePoll === 'function') startTriagePoll();
    } else {
        if (typeof stopTriagePoll === 'function') stopTriagePoll();
    }
    if (tab === 'orders' && typeof loadWorkOrders === 'function') {
        loadWorkOrders();
    }
    if (tab === 'create' && typeof initCreateForm === 'function') {
        initCreateForm();
    }
}

// ============================================================
// Queue-status helpers — used by the Print modal and Triage row
// dispatch. (The bridge shims to the deleted queue.js functions
// from 2.5b are gone in 2.5c — the WO Detail page is its own
// surface and doesn't need them.)
// ============================================================
function isQueueActiveStatus(status) {
    return ['uploading', 'uploaded', 'starting', 'printing'].indexOf(status) !== -1;
}

function isQueueFailureStatus(status) {
    return ['upload_failed', 'start_failed', 'failed'].indexOf(status) !== -1;
}

function isQueuePrintableStatus(status) {
    return status === 'queued' || status === 'failed' || status === 'cancelled';
}

function isQueueRetrySessionStatus(status) {
    return status === 'upload_failed' || status === 'start_failed';
}

function isQueueCancellableStatus(status) {
    return ['queued', 'uploading', 'uploaded', 'starting', 'printing',
            'failed', 'upload_failed', 'start_failed'].indexOf(status) !== -1;
}

function isQueueRetryableStatus(status) {
    return status === 'cancelled' || isQueueFailureStatus(status);
}
