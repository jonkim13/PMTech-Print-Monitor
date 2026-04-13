// ============================================================
// Work Orders - Page initialization, tab switching, state
// ============================================================

var _woLineItemCounter = 0;
var _woDetailId = null;
var _woDetailQueueItems = [];
var _woDetailJobs = [];
var _woSelectedQueueIds = {};

function loadWorkOrdersPage() {
    loadQueue();
    loadQueueStats();
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

function formatQueueStatusLabel(status) {
    var map = {
        queued: 'Queued',
        uploading: 'Uploading to printer',
        uploaded: 'Uploaded to printer',
        starting: 'Starting print',
        printing: 'Print started',
        completed: 'Completed',
        upload_failed: 'Upload failed',
        start_failed: 'Start failed',
        failed: 'Print failed',
        cancelled: 'Cancelled'
    };
    return map[status] || status || 'unknown';
}

function getQueueStatusClass(status) {
    var map = {
        queued: 'qs-queued',
        assigned: 'qs-assigned',
        uploading: 'qs-assigned',
        uploaded: 'qs-assigned',
        starting: 'qs-printing',
        printing: 'qs-printing',
        completed: 'qs-completed',
        upload_failed: 'qs-failed',
        start_failed: 'qs-failed',
        failed: 'qs-failed'
    };
    return map[status] || '';
}

function getWoStatusClass(status) {
    var map = {
        open: 'wos-open',
        in_progress: 'wos-inprogress',
        completed: 'wos-completed',
        cancelled: 'wos-cancelled',
        failed: 'wos-cancelled',
        attention: 'wos-open'
    };
    return map[status] || '';
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
        _woDetailQueueItems = [];
        _woDetailJobs = [];
        _woSelectedQueueIds = {};
        updateWoSelectionToolbar();
    }

    if (tab === 'queue') { loadQueue(); loadQueueStats(); }
    if (tab === 'orders') loadWorkOrders();
    if (tab === 'create') initCreateForm();
}
