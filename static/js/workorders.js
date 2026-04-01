// ============================================================
// Work Orders - Queue, Orders, Create, Detail, Print
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

// ============================================================
// Queue Stats
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

// ============================================================
// Production Queue
// ============================================================
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
            } else if (qi.status === 'failed') {
                actions = '<button class="btn btn-orange" style="font-size:10px;padding:3px 8px;" onclick="requeueItem(' + qi.queue_id + ')">Re-queue</button>' +
                    ' <button class="btn btn-green" style="font-size:10px;padding:3px 8px;" onclick="showQueuePrintModal(' + qi.queue_id + ')">Retry</button>';
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
                '<td><span class="queue-status ' + statusClass + '">' + escapeHtml(qi.status) + '</span></td>' +
                '<td>' + escapeHtml(printerText) + '</td>' +
                '<td>' + actions + '</td>' +
                '</tr>';
        }).join('');
    } catch (e) {
        document.getElementById('queueBody').innerHTML =
            '<tr><td colspan="9" class="table-empty">Error loading queue</td></tr>';
    }
}

function getQueueStatusClass(status) {
    var map = {
        queued: 'qs-queued',
        assigned: 'qs-assigned',
        printing: 'qs-printing',
        completed: 'qs-completed',
        failed: 'qs-failed'
    };
    return map[status] || '';
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

function getPrintableQueueItems() {
    return (_woDetailQueueItems || []).filter(function(qi) {
        return qi.status === 'queued' || qi.status === 'failed';
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
            (qi.status === 'queued' || qi.status === 'failed');
    });

    if (!printableItems.length) {
        showToast('No queued parts remain in this job', 'error');
        return;
    }

    showQueuePrintModal([], jobId);
}

// ============================================================
// Print from Queue Modal
// ============================================================
async function showQueuePrintModal(queueId, jobId) {
    var modal = document.getElementById('queuePrintModal');
    var queueIds = Array.isArray(queueId)
        ? queueId.filter(function(value) { return value !== null && value !== undefined && value !== ''; })
        : (queueId !== null && queueId !== undefined && queueId !== '' ? [queueId] : []);
    var isJobExecution = !!jobId;
    modal.dataset.queueIds = queueIds.join(',');
    modal.dataset.jobId = jobId || '';
    document.getElementById('queuePrintTitle').textContent = isJobExecution
        ? 'Print Job #' + jobId
        : 'Print Queue Item';
    document.getElementById('queuePrintInfo').innerHTML = '';

    // Load queue item info
    try {
        var items = (_woDetailQueueItems && _woDetailQueueItems.length)
            ? _woDetailQueueItems.slice()
            : await (await fetch('/api/queue')).json();
        var selected = [];

        if (isJobExecution) {
            selected = items.filter(function(item) {
                return String(item.job_id || '') === String(jobId);
            });
        } else {
            selected = queueIds.map(function(id) {
                return items.find(function(item) { return item.queue_id === id; });
            }).filter(function(item) {
                return !!item;
            });
        }

        if (selected.length) {
            var first = selected[0];
            var printable = isJobExecution
                ? selected.filter(function(qi) {
                    return qi.status === 'queued' || qi.status === 'failed';
                })
                : selected;
            var partLabels = printable.map(function(qi) {
                return qi.part_name + ' (' +
                    qi.sequence_number + '/' + qi.total_quantity + ')';
            });
            var materials = {};
            printable.forEach(function(qi) {
                materials[qi.material] = true;
            });

            var infoHtml = '<strong>' +
                escapeHtml((isJobExecution
                    ? 'Job #' + jobId + ' execution'
                    : selected.length + ' selected part' +
                        (selected.length === 1 ? '' : 's'))) +
                '</strong><br>';

            if (isJobExecution) {
                infoHtml += escapeHtml(printable.length + ' printable part' +
                    (printable.length === 1 ? '' : 's') +
                    ' will start as one execution') + '<br>';
                if (partLabels.length) {
                    infoHtml += escapeHtml(partLabels.join(', ')) + '<br>';
                }
            } else if (partLabels.length) {
                infoHtml += escapeHtml(partLabels.join(', ')) + '<br>';
            }

            infoHtml += 'Customer: ' +
                escapeHtml(first.customer_name) + ' | WO: ' +
                escapeHtml(first.wo_id);
            if (first.job_id) {
                infoHtml += ' | WO Job: #' + escapeHtml(String(first.job_id));
            }

            var materialList = Object.keys(materials);
            if (materialList.length === 1) {
                infoHtml += ' | Material: ' + escapeHtml(materialList[0]);
            } else if (materialList.length > 1) {
                infoHtml += '<br>Materials: ' +
                    escapeHtml(materialList.join(', '));
            }

            document.getElementById('queuePrintInfo').innerHTML = infoHtml;
        }
    } catch (e) { /* ignore */ }

    // Load idle printers
    var printerSel = document.getElementById('queuePrintPrinter');
    printerSel.innerHTML = '<option value="">Loading...</option>';

    try {
        var resp = await fetch('/api/printers');
        var printers = await resp.json();
        var idle = printers.filter(function(p) {
            return p.status === 'idle' || p.status === 'finished';
        });

        if (idle.length === 0) {
            printerSel.innerHTML = '<option value="">No idle printers available</option>';
        } else {
            printerSel.innerHTML = '<option value="">-- Select printer --</option>' +
                idle.map(function(p) {
                    return '<option value="' + escapeHtml(p.printer_id) + '">' +
                        escapeHtml(p.name) + ' (' + escapeHtml(p.model) + ') — ' +
                        escapeHtml(p.status) + '</option>';
                }).join('');
        }
    } catch (e) {
        printerSel.innerHTML = '<option value="">Error loading printers</option>';
    }

    document.getElementById('queuePrintFile').value = '';
    document.getElementById('queuePrintOperatorInitials').value = '';
    showModal('queuePrintModal');
}

async function submitQueuePrint() {
    var modal = document.getElementById('queuePrintModal');
    var queueIds = (modal.dataset.queueIds || '')
        .split(',')
        .map(function(value) { return value.trim(); })
        .filter(function(value) { return !!value; });
    var jobId = modal.dataset.jobId || '';
    var printerId = document.getElementById('queuePrintPrinter').value;
    var fileInput = document.getElementById('queuePrintFile');
    var operatorInput = document.getElementById('queuePrintOperatorInitials');
    var operatorInitials = operatorInput.value.trim();

    if (!queueIds.length && !jobId) {
        showToast('Please select at least one part', 'error');
        return;
    }
    if (!printerId) {
        showToast('Please select a printer', 'error');
        return;
    }
    if (!fileInput.files.length) {
        showToast('Please select a GCode file', 'error');
        return;
    }
    if (!operatorInitials) {
        showToast('Operator initials are required to start a print', 'error');
        operatorInput.focus();
        return;
    }

    var btn = document.getElementById('queuePrintBtn');
    btn.textContent = 'Uploading...';
    btn.disabled = true;

    try {
        var formData = new FormData();
        formData.append('printer_id', printerId);
        formData.append('file', fileInput.files[0]);
        formData.append('operator_initials', operatorInitials);
        if (jobId) {
            formData.append('job_id', jobId);
        }
        queueIds.forEach(function(queueId) {
            formData.append('queue_ids', queueId);
        });

        var resp = await fetch('/api/queue/print', {
            method: 'POST',
            body: formData
        });
        var result = await resp.json();

        if (result.success) {
            showToast(result.message || 'Print started');
            hideModal('queuePrintModal');
            clearWoSelection();
            if (_woDetailId) {
                var detailPanel = document.getElementById('woPanel-detail');
                if (detailPanel && detailPanel.classList.contains('active')) {
                    viewWorkOrder(_woDetailId);
                }
            }
            loadQueue();
            loadQueueStats();
        } else {
            showToast('Error: ' + (result.error || 'Unknown'), 'error');
        }
    } catch (e) {
        showToast('Error: ' + e.message, 'error');
    } finally {
        btn.textContent = 'Send & Print';
        btn.disabled = false;
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
// Work Order Detail
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
        if (qi.status === 'printing' || qi.status === 'assigned') {
            summary.printing_parts += 1;
        }
        if (qi.status === 'failed') summary.failed_parts += 1;
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
        return status === 'printing' || status === 'assigned';
    })) {
        summary.status = 'in_progress';
    } else if (activeStatuses.some(function(status) {
        return status === 'failed';
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
    var canSelect = qi.status === 'queued' || qi.status === 'failed';
    var selector = canSelect
        ? '<input type="checkbox" class="wo-queue-select" data-queue-id="' + qi.queue_id + '" onchange="toggleWoQueueSelection(' + qi.queue_id + ', this.checked)">'
        : '';
    var printArgs = qi.job_id ? (qi.queue_id + ', ' + qi.job_id) : qi.queue_id;

    if (qi.status === 'queued') {
        actions = '<button class="btn btn-green" style="font-size:10px;padding:3px 8px;" onclick="showQueuePrintModal(' + printArgs + ')">Print</button>';
    } else if (qi.status === 'failed') {
        actions = '<button class="btn btn-orange" style="font-size:10px;padding:3px 8px;" onclick="requeueItem(' + qi.queue_id + ')">Re-queue</button>' +
            ' <button class="btn btn-green" style="font-size:10px;padding:3px 8px;" onclick="showQueuePrintModal(' + printArgs + ')">Retry</button>';
    }

    return '<tr>' +
        '<td>' + selector + '</td>' +
        '<td>' + escapeHtml(qi.part_name) + '</td>' +
        '<td>' + escapeHtml(qi.material) + '</td>' +
        '<td>' + qi.sequence_number + '/' + qi.total_quantity + '</td>' +
        '<td><span class="queue-status ' + sc + '">' + escapeHtml(qi.status) + '</span></td>' +
        '<td>' + escapeHtml(qi.assigned_printer_name || '-') + '</td>' +
        '<td>' + escapeHtml(qi.gcode_file || '-') + '</td>' +
        '<td>' + actions + '</td>' +
        '</tr>';
}

function renderWoJobCard(title, job, items, isUnassigned) {
    var printableCount = items.filter(function(qi) {
        return qi.status === 'queued' || qi.status === 'failed';
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

// ============================================================
// Create Work Order
// ============================================================
function initCreateForm() {
    if (document.querySelectorAll('.wo-line-item').length === 0) {
        _woLineItemCounter = 0;
        addWoLineItem();
    }
}

function addWoLineItem() {
    _woLineItemCounter++;
    var id = _woLineItemCounter;
    var container = document.getElementById('woLineItems');

    var div = document.createElement('div');
    div.className = 'wo-line-item';
    div.id = 'woLineItem-' + id;
    div.innerHTML =
        '<div class="form-row">' +
        '<div class="form-group" style="flex:2;">' +
        '<label class="form-label">Part Name</label>' +
        '<input type="text" class="form-input wo-part-name" placeholder="e.g. Widget Bracket" required>' +
        '</div>' +
        '<div class="form-group" style="flex:1;">' +
        '<label class="form-label">Material</label>' +
        '<select class="form-input wo-material"><option value="">Select...</option></select>' +
        '</div>' +
        '<div class="form-group" style="flex:0 0 90px;">' +
        '<label class="form-label">Qty</label>' +
        '<input type="number" class="form-input wo-quantity" min="1" value="1" required>' +
        '</div>' +
        '<div class="form-group" style="flex:0 0 40px;display:flex;align-items:flex-end;">' +
        '<button type="button" class="btn btn-danger" style="font-size:11px;padding:5px 8px;" onclick="removeWoLineItem(' + id + ')">X</button>' +
        '</div>' +
        '</div>';
    container.appendChild(div);

    // Populate material dropdown
    loadMaterialsForLineItem(div.querySelector('.wo-material'));
}

async function loadMaterialsForLineItem(selectEl) {
    try {
        var resp = await fetch('/api/inventory/options');
        var options = await resp.json();
        var materials = options.materials || [];
        selectEl.innerHTML = '<option value="">Select...</option>' +
            materials.map(function(m) {
                return '<option value="' + escapeHtml(m) + '">' + escapeHtml(m) + '</option>';
            }).join('');
    } catch (e) {
        selectEl.innerHTML = '<option value="">Error</option>';
    }
}

function removeWoLineItem(id) {
    var el = document.getElementById('woLineItem-' + id);
    if (el) el.remove();
    // Ensure at least one remains
    if (document.querySelectorAll('.wo-line-item').length === 0) {
        addWoLineItem();
    }
}

async function submitCreateWorkOrder() {
    var customer = document.getElementById('woCustomerName').value.trim();
    if (!customer) {
        showToast('Please enter a customer name', 'error');
        return;
    }

    var lineItemEls = document.querySelectorAll('.wo-line-item');
    var lineItems = [];
    var valid = true;

    lineItemEls.forEach(function(el) {
        var partName = el.querySelector('.wo-part-name').value.trim();
        var material = el.querySelector('.wo-material').value;
        var quantity = parseInt(el.querySelector('.wo-quantity').value) || 0;

        if (!partName || !material || quantity < 1) {
            valid = false;
            return;
        }

        lineItems.push({
            part_name: partName,
            material: material,
            quantity: quantity
        });
    });

    if (!valid || lineItems.length === 0) {
        showToast('Please fill in all line item fields', 'error');
        return;
    }

    try {
        var resp = await fetch('/api/workorders', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({
                customer_name: customer,
                line_items: lineItems
            })
        });
        var result = await resp.json();

        if (result.wo_id) {
            showToast('Work order ' + result.wo_id + ' created');
            // Reset form
            document.getElementById('woCustomerName').value = '';
            document.getElementById('woLineItems').innerHTML = '';
            _woLineItemCounter = 0;
            addWoLineItem();
            // Switch to queue view
            switchWoTab('queue');
        } else {
            showToast('Error: ' + (result.error || 'Unknown'), 'error');
        }
    } catch (e) {
        showToast('Error: ' + e.message, 'error');
    }
}
