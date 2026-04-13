// ============================================================
// Work Orders - Detail view, line items, queue items within order
// ============================================================

function getPrintableQueueItems() {
    return (_woDetailQueueItems || []).filter(function(qi) {
        return isQueuePrintableStatus(qi.status);
    });
}

function syncWoSelectionCheckboxes() {
    document.querySelectorAll('.wo-queue-select').forEach(function(input) {
        var queueId = input.getAttribute('data-queue-id');
        input.checked = !!_woSelectedQueueIds[queueId];
    });
}

function getSelectedWoQueueItems() {
    return (_woDetailQueueItems || []).filter(function(qi) {
        return !!_woSelectedQueueIds[String(qi.queue_id)];
    });
}

function getSelectedWoPersistedJobIds(items) {
    var ids = {};
    (items || []).forEach(function(qi) {
        if (qi.job_id) {
            ids[String(qi.job_id)] = true;
        }
    });
    return Object.keys(ids);
}

function updateWoSelectionToolbar() {
    var bar = document.getElementById('woDetailSelectionBar');
    var text = document.getElementById('woDetailSelectionText');
    var printBtn = document.getElementById('woPrintSelectedBtn');
    var createBtn = document.getElementById('woCreateJobBtn');
    var clearBtn = document.getElementById('woClearSelectedBtn');
    var selectAll = document.getElementById('woSelectAllParts');
    if (!bar || !text || !printBtn || !createBtn || !clearBtn) {
        return;
    }

    var printable = getPrintableQueueItems();
    var selectedItems = getSelectedWoQueueItems();
    var selectedIds = Object.keys(_woSelectedQueueIds);
    var selectedJobIds = getSelectedWoPersistedJobIds(selectedItems);
    var canPrintSelection = selectedJobIds.length <= 1;

    if (printable.length === 0) {
        bar.style.display = 'none';
        text.textContent = '0 selected';
        printBtn.disabled = true;
        createBtn.disabled = true;
        clearBtn.disabled = true;
        if (selectAll) {
            selectAll.checked = false;
            selectAll.indeterminate = false;
            selectAll.disabled = true;
        }
        return;
    }

    bar.style.display = '';
    if (!selectedIds.length) {
        text.textContent = '0 selected';
    } else if (!canPrintSelection) {
        text.textContent = selectedIds.length +
            ' selected across multiple jobs';
    } else if (selectedJobIds.length === 1) {
        text.textContent = selectedIds.length + ' selected in Job #' +
            selectedJobIds[0];
    } else {
        text.textContent = selectedIds.length + ' selected';
    }
    printBtn.disabled = selectedIds.length === 0 || !canPrintSelection;
    createBtn.disabled = selectedIds.length === 0;
    clearBtn.disabled = selectedIds.length === 0;

    if (selectAll) {
        selectAll.disabled = false;
        selectAll.checked = selectedIds.length > 0 &&
            selectedIds.length === printable.length;
        selectAll.indeterminate = selectedIds.length > 0 &&
            selectedIds.length < printable.length;
    }
}

function clearWoSelection() {
    _woSelectedQueueIds = {};
    syncWoSelectionCheckboxes();
    updateWoSelectionToolbar();
}

function toggleWoQueueSelection(queueId, checked) {
    var key = String(queueId);
    if (checked) {
        _woSelectedQueueIds[key] = true;
    } else {
        delete _woSelectedQueueIds[key];
    }
    updateWoSelectionToolbar();
}

function toggleAllWoQueueSelections(checked) {
    if (!checked) {
        clearWoSelection();
        return;
    }

    _woSelectedQueueIds = {};
    getPrintableQueueItems().forEach(function(qi) {
        _woSelectedQueueIds[String(qi.queue_id)] = true;
    });
    syncWoSelectionCheckboxes();
    updateWoSelectionToolbar();
}

function printSelectedWoParts() {
    var queueIds = Object.keys(_woSelectedQueueIds).map(function(id) {
        return parseInt(id, 10);
    }).filter(function(id) {
        return !isNaN(id);
    });

    if (queueIds.length === 0) {
        showToast('Select at least one part to print', 'error');
        return;
    }

    var selectedJobIds = getSelectedWoPersistedJobIds(getSelectedWoQueueItems());
    if (selectedJobIds.length > 1) {
        showToast('Selected parts must stay within one job before printing', 'error');
        return;
    }

    showQueuePrintModal(queueIds, selectedJobIds[0] || '');
}

async function createWoJobFromSelected() {
    if (!_woDetailId) {
        return;
    }

    var queueIds = Object.keys(_woSelectedQueueIds).map(function(id) {
        return parseInt(id, 10);
    }).filter(function(id) {
        return !isNaN(id);
    });

    if (queueIds.length === 0) {
        showToast('Select at least one part to create a job', 'error');
        return;
    }

    try {
        var resp = await fetch('/api/workorders/' + encodeURIComponent(_woDetailId) + '/jobs', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ queue_ids: queueIds })
        });
        var result = await resp.json();

        if (result.success && result.job) {
            showToast('Job #' + result.job.job_id + ' created with ' +
                (result.assigned_count || queueIds.length) + ' part' +
                ((result.assigned_count || queueIds.length) === 1 ? '' : 's'));
            clearWoSelection();
            await viewWorkOrder(_woDetailId);
            loadWorkOrders();
            loadQueue();
        } else {
            showToast('Error: ' + (result.error || 'Unknown'), 'error');
        }
    } catch (e) {
        showToast('Error: ' + e.message, 'error');
    }
}

function printWoJob(jobId) {
    var printableItems = (_woDetailQueueItems || []).filter(function(qi) {
        return qi.job_id === jobId &&
            isQueuePrintableStatus(qi.status);
    });

    if (!printableItems.length) {
        showToast('No queued parts remain in this job', 'error');
        return;
    }

    showQueuePrintModal([], jobId);
}

// ============================================================
// Work Orders List
// ============================================================
async function loadWorkOrders() {
    var statusFilter = document.getElementById('woFilterStatus').value;
    var url = '/api/workorders';
    if (statusFilter) url += '?status=' + statusFilter;

    try {
        var resp = await fetch(url);
        var orders = await resp.json();
        var body = document.getElementById('workOrdersBody');

        if (orders.length === 0) {
            body.innerHTML = '<tr><td colspan="7" class="table-empty">No work orders found</td></tr>';
            return;
        }

        body.innerHTML = orders.map(function(wo) {
            var total = wo.total_parts || 0;
            var done = wo.completed_parts || 0;
            var pct = total > 0 ? Math.round(done / total * 100) : 0;
            var statusClass = getWoStatusClass(wo.status);

            var actions = '<button class="btn btn-primary" style="font-size:10px;padding:3px 8px;" onclick="viewWorkOrder(\'' + escapeHtml(wo.wo_id) + '\')">View</button>';
            if (wo.status === 'open' || wo.status === 'in_progress') {
                actions += ' <button class="btn btn-danger" style="font-size:10px;padding:3px 8px;" onclick="cancelWorkOrder(\'' + escapeHtml(wo.wo_id) + '\')">Cancel</button>';
            }

            return '<tr>' +
                '<td><a href="#" onclick="viewWorkOrder(\'' + escapeHtml(wo.wo_id) + '\');return false;" class="wo-link">' + escapeHtml(wo.wo_id) + '</a></td>' +
                '<td>' + escapeHtml(wo.customer_name) + '</td>' +
                '<td>' + formatDateTime(wo.created_at) + '</td>' +
                '<td>' + total + '</td>' +
                '<td><div class="wo-progress-bar"><div class="wo-progress-fill" style="width:' + pct + '%"></div></div><span class="wo-progress-text">' + done + '/' + total + '</span></td>' +
                '<td><span class="wo-status ' + statusClass + '">' + escapeHtml(wo.status) + '</span></td>' +
                '<td>' + actions + '</td>' +
                '</tr>';
        }).join('');
    } catch (e) {
        document.getElementById('workOrdersBody').innerHTML =
            '<tr><td colspan="7" class="table-empty">Error loading work orders</td></tr>';
    }
}

async function cancelWorkOrder(woId) {
    if (!confirm('Cancel work order ' + woId + '? This will cancel all queued items.')) return;

    try {
        var resp = await fetch('/api/workorders/' + woId, {
            method: 'PATCH',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ status: 'cancelled' })
        });
        var result = await resp.json();
        if (result.success) {
            showToast('Work order cancelled');
            loadWorkOrders();
            loadQueueStats();
        } else {
            showToast('Error: ' + result.error, 'error');
        }
    } catch (e) {
        showToast('Error: ' + e.message, 'error');
    }
}

// ============================================================
// Work Order Detail View
// ============================================================
function summarizeWoJobItems(items) {
    var summary = {
        status: 'open',
        part_count: items.length,
        completed_parts: 0,
        queued_parts: 0,
        printing_parts: 0,
        failed_parts: 0,
        printer_name: '',
        gcode_file: '',
        operator_initials: ''
    };

    if (!items.length) {
        return summary;
    }

    var statuses = [];
    items.forEach(function(qi) {
        statuses.push(qi.status);
        if (qi.status === 'completed') summary.completed_parts += 1;
        if (qi.status === 'queued') summary.queued_parts += 1;
        if (isQueueActiveStatus(qi.status) || qi.status === 'assigned') {
            summary.printing_parts += 1;
        }
        if (isQueueFailureStatus(qi.status)) summary.failed_parts += 1;
        if (!summary.printer_name && qi.assigned_printer_name) {
            summary.printer_name = qi.assigned_printer_name;
        }
        if (!summary.gcode_file && qi.gcode_file) {
            summary.gcode_file = qi.gcode_file;
        }
    });

    var activeStatuses = statuses.filter(function(status) {
        return status !== 'cancelled';
    });

    if (!activeStatuses.length) {
        summary.status = 'cancelled';
    } else if (activeStatuses.every(function(status) {
        return status === 'completed';
    })) {
        summary.status = 'completed';
    } else if (activeStatuses.some(function(status) {
        return isQueueActiveStatus(status) || status === 'assigned';
    })) {
        summary.status = 'in_progress';
    } else if (activeStatuses.some(function(status) {
        return isQueueFailureStatus(status);
    })) {
        summary.status = 'attention';
    } else if (activeStatuses.some(function(status) {
        return status === 'completed';
    })) {
        summary.status = 'in_progress';
    }

    return summary;
}

function renderWoQueueRow(qi) {
    var sc = getQueueStatusClass(qi.status);
    var actions = '';
    var canSelect = isQueuePrintableStatus(qi.status);
    var selector = canSelect
        ? '<input type="checkbox" class="wo-queue-select" data-queue-id="' + qi.queue_id + '" onchange="toggleWoQueueSelection(' + qi.queue_id + ', this.checked)">'
        : '';
    var printArgs = qi.job_id ? (qi.queue_id + ', ' + qi.job_id) : qi.queue_id;

    if (qi.status === 'queued') {
        actions = '<button class="btn btn-green" style="font-size:10px;padding:3px 8px;" onclick="showQueuePrintModal(' + printArgs + ')">Print</button>';
    } else if (qi.status === 'upload_failed' || qi.status === 'start_failed') {
        actions = '<button class="btn btn-green" style="font-size:10px;padding:3px 8px;" onclick="retryQueueSession(' + qi.queue_id + ')">' +
            (qi.status === 'start_failed' ? 'Retry Start' : 'Retry Upload') + '</button>' +
            ' <button class="btn btn-orange" style="font-size:10px;padding:3px 8px;" onclick="requeueItem(' + qi.queue_id + ')">Re-queue</button>';
    } else if (qi.status === 'failed') {
        actions = '<button class="btn btn-orange" style="font-size:10px;padding:3px 8px;" onclick="requeueItem(' + qi.queue_id + ')">Re-queue</button>' +
            ' <button class="btn btn-green" style="font-size:10px;padding:3px 8px;" onclick="showQueuePrintModal(' + printArgs + ')">Retry</button>';
    }

    return '<tr>' +
        '<td>' + selector + '</td>' +
        '<td>' + escapeHtml(qi.part_name) + '</td>' +
        '<td>' + escapeHtml(qi.material) + '</td>' +
        '<td>' + qi.sequence_number + '/' + qi.total_quantity + '</td>' +
        '<td><span class="queue-status ' + sc + '">' + escapeHtml(formatQueueStatusLabel(qi.status)) + '</span></td>' +
        '<td>' + escapeHtml(qi.assigned_printer_name || '-') + '</td>' +
        '<td>' + escapeHtml(qi.gcode_file || '-') + '</td>' +
        '<td>' + actions + '</td>' +
        '</tr>';
}

function renderWoJobCard(title, job, items, isUnassigned) {
    var printableCount = items.filter(function(qi) {
        return isQueuePrintableStatus(qi.status);
    }).length;
    var badgeClass = getWoStatusClass(job.status);
    var meta = [
        'Parts: ' + (job.part_count || items.length || 0),
        'Done: ' + (job.completed_parts || 0),
        'Queued: ' + (job.queued_parts || 0),
        'Printing: ' + (job.printing_parts || 0),
        'Failed: ' + (job.failed_parts || 0)
    ].join(' | ');

    var printButton = '';
    if (printableCount > 0 && !isUnassigned && job.job_id) {
        printButton = '<button class="btn btn-green" style="font-size:11px;padding:4px 10px;" onclick="printWoJob(' + job.job_id + ')">Print Job</button>';
    }

    var bodyHtml = '<div class="events-empty" style="padding: 18px 12px;">No parts assigned yet</div>';
    if (items.length) {
        bodyHtml = '<div class="table-container" style="margin-top: 12px;">' +
            '<table class="data-table">' +
            '<thead>' +
            '<tr>' +
            '<th style="width: 40px;"></th>' +
            '<th>Part</th>' +
            '<th>Material</th>' +
            '<th>Seq</th>' +
            '<th>Status</th>' +
            '<th>Printer</th>' +
            '<th>GCode</th>' +
            '<th>Actions</th>' +
            '</tr>' +
            '</thead>' +
            '<tbody>' + items.map(renderWoQueueRow).join('') + '</tbody>' +
            '</table>' +
            '</div>';
    }

    return '<div style="margin-bottom: 16px; border: 1px solid var(--border); border-radius: 10px; background: var(--surface); overflow: hidden;">' +
        '<div style="display:flex; align-items:flex-start; justify-content:space-between; gap:12px; padding: 16px 18px 12px 18px; border-bottom: 1px solid var(--border);">' +
        '<div>' +
        '<div style="display:flex; align-items:center; gap:10px; flex-wrap:wrap;">' +
        '<strong style="font-size:15px;">' + escapeHtml(title) + '</strong>' +
        '<span class="wo-status ' + badgeClass + '" style="font-size:12px;">' + escapeHtml(job.status || 'open') + '</span>' +
        '</div>' +
        '<div style="margin-top:6px; color:var(--text-secondary); font-size:12px;">' + escapeHtml(meta) + '</div>' +
        '<div style="margin-top:4px; color:var(--text-secondary); font-size:12px;">Latest print: ' +
        escapeHtml(job.printer_name || '-') + ' | File: ' +
        escapeHtml(job.gcode_file || '-') + ' | Operator: ' +
        escapeHtml(job.operator_initials || '-') + '</div>' +
        '</div>' +
        '<div>' + printButton + '</div>' +
        '</div>' +
        bodyHtml +
        '</div>';
}

function renderWoJobs(wo) {
    var container = document.getElementById('woJobsContainer');
    if (!container) {
        return;
    }

    var queueItems = wo.queue_items || [];
    var jobs = wo.jobs || [];
    var itemsByJobId = {};

    queueItems.forEach(function(qi) {
        var key = qi.job_id ? String(qi.job_id) : '__unassigned__';
        if (!itemsByJobId[key]) {
            itemsByJobId[key] = [];
        }
        itemsByJobId[key].push(qi);
    });

    var cards = jobs.map(function(job) {
        return renderWoJobCard(
            'Job #' + job.job_id,
            job,
            itemsByJobId[String(job.job_id)] || [],
            false
        );
    });

    if (itemsByJobId.__unassigned__ && itemsByJobId.__unassigned__.length) {
        cards.push(renderWoJobCard(
            'Unassigned Parts',
            summarizeWoJobItems(itemsByJobId.__unassigned__),
            itemsByJobId.__unassigned__,
            true
        ));
    }

    if (!cards.length) {
        container.innerHTML = '<div class="events-empty">No queue items</div>';
        return;
    }

    container.innerHTML = cards.join('');
    syncWoSelectionCheckboxes();
}

async function viewWorkOrder(woId) {
    _woDetailId = woId;
    _woDetailQueueItems = [];
    _woDetailJobs = [];
    _woSelectedQueueIds = {};

    // Hide all wo panels, show detail
    var panels = ['queue', 'orders', 'create'];
    panels.forEach(function(p) {
        var el = document.getElementById('woPanel-' + p);
        if (el) el.classList.remove('active');
    });
    // Remove active from tabs
    document.querySelectorAll('[data-wotab]').forEach(function(b) {
        b.classList.remove('active');
    });

    var detailPanel = document.getElementById('woPanel-detail');
    detailPanel.style.display = '';
    detailPanel.classList.add('active');

    try {
        var resp = await fetch('/api/workorders/' + woId);
        var wo = await resp.json();

        if (wo.error) {
            document.getElementById('woDetailHeader').innerHTML =
                '<div class="events-empty">' + escapeHtml(wo.error) + '</div>';
            document.getElementById('woJobsContainer').innerHTML = '';
            updateWoSelectionToolbar();
            return;
        }

        var total = wo.total_parts || 0;
        var done = wo.completed_parts || 0;
        var pct = total > 0 ? Math.round(done / total * 100) : 0;
        var statusClass = getWoStatusClass(wo.status);

        document.getElementById('woDetailHeader').innerHTML =
            '<div class="wo-detail-head">' +
            '<div><h3 style="margin:0;">' + escapeHtml(wo.wo_id) + '</h3>' +
            '<span style="color:var(--text-secondary);font-size:12px;">' + escapeHtml(wo.customer_name) + ' — ' + formatDateTime(wo.created_at) + ' — ' + escapeHtml(String(wo.job_count || (wo.jobs || []).length || 0)) + ' job' + (((wo.job_count || (wo.jobs || []).length || 0) === 1) ? '' : 's') + '</span></div>' +
            '<span class="wo-status ' + statusClass + '" style="font-size:13px;">' + escapeHtml(wo.status) + '</span>' +
            '</div>' +
            '<div class="wo-detail-progress">' +
            '<div class="wo-progress-bar" style="height:8px;flex:1;"><div class="wo-progress-fill" style="width:' + pct + '%"></div></div>' +
            '<span class="wo-progress-text">' + done + ' / ' + total + ' (' + pct + '%)</span>' +
            '</div>';

        var queueItems = wo.queue_items || [];
        _woDetailJobs = wo.jobs || [];
        _woDetailQueueItems = queueItems;
        renderWoJobs(wo);
        updateWoSelectionToolbar();
    } catch (e) {
        document.getElementById('woDetailHeader').innerHTML =
            '<div class="events-empty">Error loading work order</div>';
        document.getElementById('woJobsContainer').innerHTML = '';
        _woDetailQueueItems = [];
        _woDetailJobs = [];
        _woSelectedQueueIds = {};
        updateWoSelectionToolbar();
    }
}
