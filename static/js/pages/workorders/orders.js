// ============================================================
// Work Orders - All Orders sub-tab (list + row actions)
// Extracted from detail.js in Phase 2.5c. The WO Detail view
// is now its own route (/work-orders/<id>); rows in this list
// navigate via window.location.href rather than in-page rendering.
// ============================================================

async function loadWorkOrders() {
    var statusFilterEl = document.getElementById('woFilterStatus');
    var statusFilter = statusFilterEl ? statusFilterEl.value : '';
    var url = '/api/workorders';
    if (statusFilter) url += '?status=' + statusFilter;

    try {
        var orders = await apiGet(url);
        var body = document.getElementById('workOrdersBody');
        if (!body) return;

        if (!orders.length) {
            body.innerHTML = '<tr><td colspan="7" class="table-empty">No work orders found</td></tr>';
            return;
        }

        body.innerHTML = orders.map(function (wo) {
            var total = wo.total_parts || 0;
            var done = wo.completed_parts || 0;
            var pct = total > 0 ? Math.round(done / total * 100) : 0;
            var statusInfo = (typeof formatWoStatus === 'function')
                ? formatWoStatus(wo.status)
                : { label: wo.status || 'open', cssClass: '' };
            var detailUrl = '/work-orders/' + encodeURIComponent(wo.wo_id) + '?from=all';

            var actions = '<a class="btn btn-primary" style="font-size:10px;padding:3px 8px;" href="' +
                detailUrl + '">View</a>';
            if (wo.status === 'open' || wo.status === 'in_progress' || wo.status === 'attention') {
                actions += ' <button class="btn btn-danger" style="font-size:10px;padding:3px 8px;" onclick="cancelWorkOrder(\'' +
                    escapeHtml(wo.wo_id) + '\')">Cancel</button>';
            }
            if (wo.status === 'cancelled' || wo.status === 'attention') {
                actions += ' <button class="btn btn-orange" style="font-size:10px;padding:3px 8px;" onclick="retryWorkOrder(\'' +
                    escapeHtml(wo.wo_id) + '\')">Re-queue</button>';
            }

            return '<tr>' +
                '<td><a class="wo-link" href="' + detailUrl + '">' + escapeHtml(wo.wo_id) + '</a></td>' +
                '<td>' + escapeHtml(wo.customer_name) + '</td>' +
                '<td>' + formatDateTime(wo.created_at) + '</td>' +
                '<td>' + total + '</td>' +
                '<td><div class="wo-progress-bar"><div class="wo-progress-fill" style="width:' + pct + '%"></div></div>' +
                '<span class="wo-progress-text">' + done + '/' + total + '</span></td>' +
                '<td><span class="wo-status ' + statusInfo.cssClass + '">' + escapeHtml(statusInfo.label) + '</span></td>' +
                '<td>' + actions + '</td>' +
                '</tr>';
        }).join('');
    } catch (e) {
        var body = document.getElementById('workOrdersBody');
        if (body) {
            body.innerHTML = '<tr><td colspan="7" class="table-empty">Error loading work orders</td></tr>';
        }
    }
}

// ------------------------------------------------------------
// WO-level actions invoked from the All Orders row buttons
// (and from the WO Detail page header via WoDetail.cancelWO /
// WoDetail.retryWO which have their own modal-based flows).
// ------------------------------------------------------------

async function cancelWorkOrder(woId) {
    if (!confirm('Cancel work order ' + woId + '? This stops any active prints ' +
        'and cancels every remaining item in the order.')) return;
    try {
        var result = await apiDelete('/api/workorders/' + encodeURIComponent(woId));
        var n = result.cancelled_count || 0;
        showToast('Cancelled ' + n + ' part' + (n === 1 ? '' : 's'));
        loadWorkOrders();
        if (typeof loadTriage === 'function') loadTriage();
    } catch (e) {
        showToast('Error: ' + e.message, 'error');
    }
}

async function retryWorkOrder(woId, countHint) {
    var n = countHint || '';
    if (!confirm('Re-queue ' + (n ? n + ' ' : '') + 'cancelled/failed part' +
        (n === 1 ? '' : 's') + ' in ' + woId + '?')) return;
    try {
        var result = await apiPost('/api/workorders/' + encodeURIComponent(woId) + '/retry', {});
        var count = result.requeued_count || 0;
        showToast('Re-queued ' + count + ' part' + (count === 1 ? '' : 's'));
        loadWorkOrders();
        if (typeof loadTriage === 'function') loadTriage();
    } catch (e) {
        showToast('Error: ' + e.message, 'error');
    }
}
