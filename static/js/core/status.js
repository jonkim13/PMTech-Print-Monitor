// ============================================================
// Core - Shared status label + CSS class maps
// One source of truth for queue-item, work-order, and job status
// rendering. Views should use the `formatXxxStatus` helpers; the
// maps are exposed for callers that need to iterate (e.g. filter
// dropdown population).
// ============================================================

var QUEUE_ITEM_STATUS = {
    queued: { label: 'Queued', cssClass: 'qs-queued' },
    uploading: { label: 'Uploading', cssClass: 'qs-uploading' },
    uploaded: { label: 'Uploaded', cssClass: 'qs-uploaded' },
    starting: { label: 'Starting', cssClass: 'qs-starting' },
    printing: { label: 'Printing', cssClass: 'qs-printing' },
    completed: { label: 'Completed', cssClass: 'qs-completed' },
    failed: { label: 'Failed', cssClass: 'qs-failed' },
    upload_failed: { label: 'Upload Failed', cssClass: 'qs-failed' },
    start_failed: { label: 'Start Failed', cssClass: 'qs-failed' },
    cancelled: { label: 'Cancelled', cssClass: 'qs-cancelled' }
};

var WO_STATUS = {
    open: { label: 'Open', cssClass: 'wo-open' },
    in_progress: { label: 'In Progress', cssClass: 'wo-in-progress' },
    attention: { label: 'Needs Attention', cssClass: 'wo-attention' },
    completed: { label: 'Completed', cssClass: 'wo-completed' },
    cancelled: { label: 'Cancelled', cssClass: 'wo-cancelled' }
};

var JOB_STATUS = {
    open: { label: 'Open', cssClass: 'job-open' },
    in_progress: { label: 'In Progress', cssClass: 'job-in-progress' },
    attention: { label: 'Needs Attention', cssClass: 'job-attention' },
    completed: { label: 'Completed', cssClass: 'job-completed' },
    cancelled: { label: 'Cancelled', cssClass: 'job-cancelled' }
};

function _resolveStatus(map, status) {
    var entry = map[status];
    if (entry) {
        return entry;
    }
    var fallback = String(status || 'unknown');
    return { label: fallback, cssClass: '' };
}

function formatQueueStatus(status) {
    return _resolveStatus(QUEUE_ITEM_STATUS, status);
}

function formatWoStatus(status) {
    return _resolveStatus(WO_STATUS, status);
}

function formatJobStatus(status) {
    return _resolveStatus(JOB_STATUS, status);
}
