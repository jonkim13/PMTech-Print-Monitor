// ============================================================
// Work Orders - Queue table, stats, queue item actions
// ============================================================

async function loadQueueStats() {
    try {
        var resp = await fetch('/api/queue/stats');
        var stats = await resp.json();
        document.getElementById('queueStatQueued').textContent = stats.queued || 0;
        document.getElementById('queueStatPrinting').textContent = stats.printing || 0;
        document.getElementById('queueStatCompleted').textContent = stats.completed || 0;
        document.getElementById('queueStatFailed').textContent = stats.failed || 0;
    } catch (e) { /* ignore */ }
}

async function loadQueue() {
    var statusFilter = document.getElementById('queueFilterStatus').value;
    var url = '/api/queue';
    if (statusFilter) url += '?status=' + statusFilter;

    try {
        var resp = await fetch(url);
        var items = await resp.json();
        var body = document.getElementById('queueBody');

        if (items.length === 0) {
            body.innerHTML = '<tr><td colspan="9" class="table-empty">No items in queue</td></tr>';
            return;
        }

        body.innerHTML = items.map(function(qi, idx) {
            var statusClass = getQueueStatusClass(qi.status);
            var actions = '';

            if (qi.status === 'queued') {
                actions = '<button class="btn btn-green" style="font-size:10px;padding:3px 8px;" onclick="showQueuePrintModal(' + qi.queue_id + ')">Print</button>';
            } else if (qi.status === 'printing') {
                actions = '<button class="btn btn-danger" style="font-size:10px;padding:3px 8px;" onclick="cancelQueuePrint(' + qi.queue_id + ')">Cancel Print</button>';
            } else if (qi.status === 'upload_failed' || qi.status === 'start_failed') {
                actions = '<button class="btn btn-green" style="font-size:10px;padding:3px 8px;" onclick="retryQueueSession(' + qi.queue_id + ')">' +
                    (qi.status === 'start_failed' ? 'Retry Start' : 'Retry Upload') + '</button>' +
                    ' <button class="btn btn-orange" style="font-size:10px;padding:3px 8px;" onclick="requeueItem(' + qi.queue_id + ')">Re-queue</button>';
            } else if (qi.status === 'failed') {
                actions = '<button class="btn btn-orange" style="font-size:10px;padding:3px 8px;" onclick="requeueItem(' + qi.queue_id + ')">Re-queue</button>' +
                    ' <button class="btn btn-green" style="font-size:10px;padding:3px 8px;" onclick="showQueuePrintModal(' + qi.queue_id + ')">Retry Print</button>';
            } else if (qi.status === 'completed' && qi.print_job_id) {
                actions = '<button class="btn" style="font-size:10px;padding:3px 8px;" onclick="showJobDetail(' + qi.print_job_id + ')">View Print Log</button>';
            }

            var printerText = qi.assigned_printer_name || '-';

            return '<tr>' +
                '<td>' + (idx + 1) + '</td>' +
                '<td><a href="#" onclick="viewWorkOrder(\'' + escapeHtml(qi.wo_id) + '\');return false;" class="wo-link">' + escapeHtml(qi.wo_id) + '</a></td>' +
                '<td>' + escapeHtml(qi.customer_name) + '</td>' +
                '<td>' + escapeHtml(qi.part_name) + formatQueueJobSummary(qi) + '</td>' +
                '<td>' + escapeHtml(qi.material) + '</td>' +
                '<td>' + qi.sequence_number + '/' + qi.total_quantity + '</td>' +
                '<td><span class="queue-status ' + statusClass + '">' + escapeHtml(formatQueueStatusLabel(qi.status)) + '</span></td>' +
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
        var resp = await fetch('/api/queue/' + queueId + '/retry', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({})
        });
        var result = await resp.json();
        if (result.success || result.ok) {
            showToast(result.message || 'Retry sent to printer');
            if (_woDetailId) {
                var detailPanel = document.getElementById('woPanel-detail');
                if (detailPanel && detailPanel.classList.contains('active')) {
                    viewWorkOrder(_woDetailId);
                }
            }
            loadQueue();
            loadQueueStats();
        } else {
            showToast('Error: ' + (result.message || result.error || 'Unknown'), 'error');
        }
    } catch (e) {
        showToast('Error: ' + e.message, 'error');
    }
}

async function cancelQueuePrint(queueId) {
    if (!confirm('Cancel this print? The printer will be stopped and the item will be requeued.')) {
        return;
    }
    try {
        var resp = await fetch('/api/queue/' + queueId + '/cancel-print', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({})
        });
        var result = await resp.json();
        if (result.success) {
            showToast(result.message || 'Print cancelled');
            if (_woDetailId) {
                var detailPanel = document.getElementById('woPanel-detail');
                if (detailPanel && detailPanel.classList.contains('active')) {
                    viewWorkOrder(_woDetailId);
                }
            }
            loadQueue();
            loadQueueStats();
        } else {
            showToast('Error: ' + (result.error || result.message || 'Unknown'), 'error');
        }
    } catch (e) {
        showToast('Error: ' + e.message, 'error');
    }
}

async function requeueItem(queueId) {
    try {
        var resp = await fetch('/api/queue/' + queueId, {
            method: 'PATCH',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ status: 'queued' })
        });
        var result = await resp.json();
        if (result.success) {
            showToast('Item re-queued');
            if (_woDetailId) {
                var detailPanel = document.getElementById('woPanel-detail');
                if (detailPanel && detailPanel.classList.contains('active')) {
                    viewWorkOrder(_woDetailId);
                }
            }
            loadQueue();
            loadQueueStats();
        } else {
            showToast('Error: ' + result.error, 'error');
        }
    } catch (e) {
        showToast('Error: ' + e.message, 'error');
    }
}
