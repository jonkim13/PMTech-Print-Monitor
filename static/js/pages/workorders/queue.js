// ============================================================
// Work Orders - Queue table, stats, queue item actions
// ============================================================

function renderQueueStats(stats) {
    stats = stats || {};
    var el = function(id) { return document.getElementById(id); };
    var queued = el('queueStatQueued');
    var printing = el('queueStatPrinting');
    var inUpload = el('queueStatInUpload');
    var completed = el('queueStatCompleted');
    var failed = el('queueStatFailed');

    var uploadingCount = stats.uploading || 0;
    var uploadedCount = stats.uploaded || 0;
    var startingCount = stats.starting || 0;
    // `stats.printing` is total active (upload + uploaded + starting +
    // printing).  Subtract the upload stages to get the strictly
    // "on-printer" count.
    var activeCount = stats.printing || 0;
    var trulyPrinting = Math.max(
        0, activeCount - uploadingCount - uploadedCount - startingCount
    );

    if (queued) queued.textContent = stats.queued || 0;
    if (printing) printing.textContent = trulyPrinting;
    if (inUpload) {
        inUpload.textContent =
            uploadingCount + uploadedCount + startingCount;
    }
    if (completed) completed.textContent = stats.completed || 0;
    if (failed) failed.textContent = stats.failed || 0;
}

async function loadQueueStats() {
    try {
        var stats = await apiGet('/api/queue/stats');
        renderQueueStats(stats);
    } catch (e) { /* ignore */ }
}

async function loadQueue() {
    var statusFilter = document.getElementById('queueFilterStatus').value;
    var url = '/api/queue';
    if (statusFilter) url += '?status=' + statusFilter;

    try {
        var items = await apiGet(url);
        var body = document.getElementById('queueBody');

        if (items.length === 0) {
            body.innerHTML = '<tr><td colspan="9" class="table-empty">No items in queue</td></tr>';
            return;
        }

        body.innerHTML = items.map(function(qi, idx) {
            var statusInfo = formatQueueStatus(qi.status);
            var btnSm = 'style="font-size:10px;padding:3px 8px;"';
            var actionParts = [];

            if (qi.status === 'queued' || qi.status === 'cancelled') {
                actionParts.push('<button class="btn btn-green" ' + btnSm +
                    ' onclick="showQueuePrintModal(' + qi.queue_id + ')">Print</button>');
            } else if (qi.status === 'upload_failed' || qi.status === 'start_failed') {
                actionParts.push('<button class="btn btn-green" ' + btnSm +
                    ' onclick="retryQueueSession(' + qi.queue_id + ')">' +
                    (qi.status === 'start_failed' ? 'Retry Start' : 'Retry Upload') + '</button>');
            } else if (qi.status === 'failed') {
                actionParts.push('<button class="btn btn-green" ' + btnSm +
                    ' onclick="showQueuePrintModal(' + qi.queue_id + ')">Retry Print</button>');
            } else if (qi.status === 'completed' && qi.print_job_id) {
                actionParts.push('<button class="btn" ' + btnSm +
                    ' onclick="showJobDetail(' + qi.print_job_id + ')">View Print Log</button>');
            }

            if (isQueueCancellableStatus(qi.status)) {
                var cancelLabel = qi.status === 'printing'
                    ? 'Cancel Print' : 'Cancel';
                actionParts.push('<button class="btn btn-danger" ' + btnSm +
                    ' onclick="cancelQueueItem(' + qi.queue_id + ', \'' +
                    escapeHtml(qi.part_name) + '\')">' + cancelLabel +
                    '</button>');
            }
            var actions = actionParts.join(' ');

            var printerText = qi.assigned_printer_name || '-';

            return '<tr>' +
                '<td>' + (idx + 1) + '</td>' +
                '<td><a href="#" onclick="viewWorkOrder(\'' + escapeHtml(qi.wo_id) + '\');return false;" class="wo-link">' + escapeHtml(qi.wo_id) + '</a></td>' +
                '<td>' + escapeHtml(qi.customer_name) + '</td>' +
                '<td>' + escapeHtml(qi.part_name) + formatQueueJobSummary(qi) + '</td>' +
                '<td>' + escapeHtml(qi.material) + '</td>' +
                '<td>' + qi.sequence_number + '/' + qi.total_quantity + '</td>' +
                '<td><span class="queue-status ' + statusInfo.cssClass + '">' + escapeHtml(statusInfo.label) + '</span></td>' +
                '<td>' + escapeHtml(printerText) + '</td>' +
                '<td>' + actions + '</td>' +
                '</tr>';
        }).join('');
    } catch (e) {
        document.getElementById('queueBody').innerHTML =
            '<tr><td colspan="9" class="table-empty">Error loading queue</td></tr>';
    }
}

function formatQueueJobSummary(qi) {
    if (!qi) {
        return '';
    }

    var details = [];
    if (qi.job_id) {
        details.push('WO Job #' + qi.job_id);
    }
    var queueJobPartCount = qi.queue_job_part_count || qi.job_part_count || 0;
    var queueJobPartNames = qi.queue_job_part_names || qi.job_part_names || '';
    if (queueJobPartCount > 1) {
        var activeSummary = 'Print session: ' + queueJobPartCount + ' parts';
        if (queueJobPartNames) {
            activeSummary += ' - ' + queueJobPartNames;
        }
        details.push(activeSummary);
    }

    if (!details.length) {
        return '';
    }

    return '<div style="font-size:10px;color:var(--text-secondary);margin-top:2px;">' +
        escapeHtml(details.join(' | ')) +
        '</div>';
}

async function retryQueueSession(queueId) {
    try {
        var result = await apiPost('/api/queue/' + queueId + '/retry', {});
        showToast(result.message || 'Retry sent to printer');
        refreshQueueViews();
    } catch (e) {
        showToast('Error: ' + e.message, 'error');
    }
}

async function requeueItem(queueId) {
    try {
        await apiPatch('/api/queue/' + queueId, { status: 'queued' });
        showToast('Item re-queued');
        refreshQueueViews();
    } catch (e) {
        showToast('Error: ' + e.message, 'error');
    }
}

function refreshQueueViews() {
    if (_woDetailId) {
        var detailPanel = document.getElementById('woPanel-detail');
        if (detailPanel && detailPanel.classList.contains('active')) {
            viewWorkOrder(_woDetailId);
        }
    }
    loadQueue();
    loadQueueStats();
    // Keep the WO list in sync — every queue transition potentially
    // rolls up into a new WO status.
    loadWorkOrders();
}
