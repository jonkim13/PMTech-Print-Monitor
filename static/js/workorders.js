// ============================================================
// Work Orders - Queue, Orders, Create, Detail, Print
// ============================================================

var _woLineItemCounter = 0;
var _woDetailId = null;

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
                actions = '<button class="btn" style="font-size:10px;padding:3px 8px;" onclick="showJobDetail(' + qi.print_job_id + ')">View Job</button>';
            }

            var printerText = qi.assigned_printer_name || '-';

            return '<tr>' +
                '<td>' + (idx + 1) + '</td>' +
                '<td><a href="#" onclick="viewWorkOrder(\'' + escapeHtml(qi.wo_id) + '\');return false;" class="wo-link">' + escapeHtml(qi.wo_id) + '</a></td>' +
                '<td>' + escapeHtml(qi.customer_name) + '</td>' +
                '<td>' + escapeHtml(qi.part_name) + '</td>' +
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

// ============================================================
// Print from Queue Modal
// ============================================================
async function showQueuePrintModal(queueId) {
    var modal = document.getElementById('queuePrintModal');
    modal.dataset.queueId = queueId;

    // Load queue item info
    try {
        var items = await (await fetch('/api/queue')).json();
        var qi = items.find(function(i) { return i.queue_id === queueId; });
        if (qi) {
            document.getElementById('queuePrintInfo').innerHTML =
                '<strong>' + escapeHtml(qi.part_name) + '</strong> (' +
                qi.sequence_number + '/' + qi.total_quantity + ') — ' +
                escapeHtml(qi.material) + '<br>Customer: ' +
                escapeHtml(qi.customer_name) + ' | WO: ' + escapeHtml(qi.wo_id);
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
    var queueId = document.getElementById('queuePrintModal').dataset.queueId;
    var printerId = document.getElementById('queuePrintPrinter').value;
    var fileInput = document.getElementById('queuePrintFile');
    var operatorInput = document.getElementById('queuePrintOperatorInitials');
    var operatorInitials = operatorInput.value.trim();

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

        var resp = await fetch('/api/queue/' + queueId + '/print', {
            method: 'POST',
            body: formData
        });
        var result = await resp.json();

        if (result.success) {
            showToast(result.message || 'Print started');
            hideModal('queuePrintModal');
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
        cancelled: 'wos-cancelled'
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
async function viewWorkOrder(woId) {
    _woDetailId = woId;

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
            return;
        }

        var total = wo.total_parts || 0;
        var done = wo.completed_parts || 0;
        var pct = total > 0 ? Math.round(done / total * 100) : 0;
        var statusClass = getWoStatusClass(wo.status);

        document.getElementById('woDetailHeader').innerHTML =
            '<div class="wo-detail-head">' +
            '<div><h3 style="margin:0;">' + escapeHtml(wo.wo_id) + '</h3>' +
            '<span style="color:var(--text-secondary);font-size:12px;">' + escapeHtml(wo.customer_name) + ' — ' + formatDateTime(wo.created_at) + '</span></div>' +
            '<span class="wo-status ' + statusClass + '" style="font-size:13px;">' + escapeHtml(wo.status) + '</span>' +
            '</div>' +
            '<div class="wo-detail-progress">' +
            '<div class="wo-progress-bar" style="height:8px;flex:1;"><div class="wo-progress-fill" style="width:' + pct + '%"></div></div>' +
            '<span class="wo-progress-text">' + done + ' / ' + total + ' (' + pct + '%)</span>' +
            '</div>';

        var queueItems = wo.queue_items || [];
        var body = document.getElementById('woDetailBody');

        if (queueItems.length === 0) {
            body.innerHTML = '<tr><td colspan="7" class="table-empty">No queue items</td></tr>';
            return;
        }

        body.innerHTML = queueItems.map(function(qi) {
            var sc = getQueueStatusClass(qi.status);
            var actions = '';
            if (qi.status === 'queued') {
                actions = '<button class="btn btn-green" style="font-size:10px;padding:3px 8px;" onclick="showQueuePrintModal(' + qi.queue_id + ')">Print</button>';
            } else if (qi.status === 'failed') {
                actions = '<button class="btn btn-orange" style="font-size:10px;padding:3px 8px;" onclick="requeueItem(' + qi.queue_id + ')">Re-queue</button>' +
                    ' <button class="btn btn-green" style="font-size:10px;padding:3px 8px;" onclick="showQueuePrintModal(' + qi.queue_id + ')">Retry</button>';
            }

            return '<tr>' +
                '<td>' + escapeHtml(qi.part_name) + '</td>' +
                '<td>' + escapeHtml(qi.material) + '</td>' +
                '<td>' + qi.sequence_number + '/' + qi.total_quantity + '</td>' +
                '<td><span class="queue-status ' + sc + '">' + escapeHtml(qi.status) + '</span></td>' +
                '<td>' + escapeHtml(qi.assigned_printer_name || '-') + '</td>' +
                '<td>' + escapeHtml(qi.gcode_file || '-') + '</td>' +
                '<td>' + actions + '</td>' +
                '</tr>';
        }).join('');
    } catch (e) {
        document.getElementById('woDetailHeader').innerHTML =
            '<div class="events-empty">Error loading work order</div>';
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
