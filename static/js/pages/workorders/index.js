// ============================================================
// Work Orders - Page initialization, tab switching, state
// ============================================================

var _woLineItemCounter = 0;
var _woDetailId = null;
var _woDetailQueueItems = [];
var _woDetailJobs = [];
var _woSelectedQueueIds = {};
var _woQueueRefreshTimer = null;
var _woDetailRefreshTimer = null;

function _woRefreshIntervalMs() {
    var cfg = (typeof window !== 'undefined' && window.APP_CONFIG)
        ? window.APP_CONFIG : {};
    var base = cfg.pollIntervalMs;
    if (typeof base !== 'number' || base <= 0) {
        return 10000;
    }
    // Use 2x the dashboard poll cadence for WO views so we don't
    // over-fetch from a user parking on the page.
    return Math.max(5000, base * 2);
}

function startWoQueueAutoRefresh() {
    stopWoQueueAutoRefresh();
    _woQueueRefreshTimer = setInterval(function() {
        loadQueue();
        loadQueueStats();
    }, _woRefreshIntervalMs());
}

function stopWoQueueAutoRefresh() {
    if (_woQueueRefreshTimer !== null) {
        clearInterval(_woQueueRefreshTimer);
        _woQueueRefreshTimer = null;
    }
}

function startWoDetailAutoRefresh() {
    stopWoDetailAutoRefresh();
    _woDetailRefreshTimer = setInterval(function() {
        if (_woDetailId) {
            viewWorkOrder(_woDetailId);
        } else {
            stopWoDetailAutoRefresh();
        }
    }, _woRefreshIntervalMs());
}

function stopWoDetailAutoRefresh() {
    if (_woDetailRefreshTimer !== null) {
        clearInterval(_woDetailRefreshTimer);
        _woDetailRefreshTimer = null;
    }
}

function loadWorkOrdersPage() {
    loadQueue();
    loadQueueStats();
    startWoQueueAutoRefresh();
}

function isQueueActiveStatus(status) {
    return ['uploading', 'uploaded', 'starting', 'printing'].indexOf(status) !== -1;
}

function isQueueFailureStatus(status) {
    return ['upload_failed', 'start_failed', 'failed'].indexOf(status) !== -1;
}

function isQueuePrintableStatus(status) {
    return status === 'queued' || status === 'failed';
}

function isQueueRetrySessionStatus(status) {
    return status === 'upload_failed' || status === 'start_failed';
}

// ============================================================
// Sub-tab switching
// ============================================================
function switchWoTab(tab) {
    document.querySelectorAll('[data-wotab]').forEach(function(b) {
        b.classList.remove('active');
    });
    var btn = document.querySelector('[data-wotab="' + tab + '"]');
    if (btn) btn.classList.add('active');

    // Hide all panels
    var panels = ['queue', 'orders', 'create', 'detail'];
    panels.forEach(function(p) {
        var el = document.getElementById('woPanel-' + p);
        if (el) {
            el.classList.remove('active');
            el.style.display = '';
        }
    });

    // Show selected panel
    var panel = document.getElementById('woPanel-' + tab);
    if (panel) {
        panel.classList.add('active');
        if (tab === 'detail') panel.style.display = '';
    }

    if (tab !== 'detail') {
        _woDetailId = null;
        _woDetailQueueItems = [];
        _woDetailJobs = [];
        _woSelectedQueueIds = {};
        stopWoDetailAutoRefresh();
        updateWoSelectionToolbar();
    }

    if (tab === 'queue') {
        loadQueue();
        loadQueueStats();
        startWoQueueAutoRefresh();
    } else {
        stopWoQueueAutoRefresh();
    }
    if (tab === 'orders') loadWorkOrders();
    if (tab === 'create') initCreateForm();
}
