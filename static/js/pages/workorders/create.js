// ============================================================
// Work Orders - Create form: typed job groups, submission.
//
// Phase G — the New WO page builds a whole order in one submit. Each
// "job group" carries a type selector:
//   Internal → Part Name + Material + Qty  (→ line_items / queue_items)
//   External → Vendor + Process            (→ a non-printing job row)
//   Design   → Designer + Requirements     (→ a non-printing job row)
// An order can mix types freely or use just one. On submit the groups
// are split into `line_items` (Internal) + `jobs` (External/Design) and
// POSTed to /api/workorders, which creates everything atomically.
// (_woLineItemCounter is declared in index.js and reused as the group id.)
// ============================================================

function initCreateForm() {
    if (document.querySelectorAll('.wo-job-group').length === 0) {
        _woLineItemCounter = 0;
        addWoJobGroup('Internal');
    }
}

function _woGroupFieldsHtml(type) {
    if (type === 'External') {
        return '' +
            '<div class="form-group" style="flex:1;">' +
            '<label class="form-label">Vendor</label>' +
            '<input type="text" class="form-input wo-vendor" maxlength="120" placeholder="e.g. MachiningCo">' +
            '</div>' +
            '<div class="form-group" style="flex:1;">' +
            '<label class="form-label">Process</label>' +
            '<input type="text" class="form-input wo-process" maxlength="120" placeholder="e.g. CNC Mill / Anodize">' +
            '</div>';
    }
    if (type === 'Design') {
        return '' +
            '<div class="form-group" style="flex:1;">' +
            '<label class="form-label">Designer</label>' +
            '<input type="text" class="form-input wo-designer" maxlength="120" placeholder="Designer name">' +
            '</div>' +
            '<div class="form-group" style="flex:2;">' +
            '<label class="form-label">Requirements (optional)</label>' +
            '<textarea class="form-input wo-requirements" rows="2" placeholder="Optional"></textarea>' +
            '</div>';
    }
    // Internal (default)
    return '' +
        '<div class="form-group" style="flex:2;">' +
        '<label class="form-label">Part Name</label>' +
        '<input type="text" class="form-input wo-part-name" placeholder="e.g. Widget Bracket">' +
        '</div>' +
        '<div class="form-group" style="flex:1;">' +
        '<label class="form-label">Material</label>' +
        '<select class="form-input wo-material"><option value="">Select...</option></select>' +
        '</div>' +
        '<div class="form-group" style="flex:0 0 90px;">' +
        '<label class="form-label">Qty</label>' +
        '<input type="number" class="form-input wo-quantity" min="1" value="1">' +
        '</div>';
}

function addWoJobGroup(type) {
    type = type || 'Internal';
    _woLineItemCounter++;
    var id = _woLineItemCounter;
    var container = document.getElementById('woJobGroups');

    var div = document.createElement('div');
    div.className = 'wo-job-group';
    div.id = 'woJobGroup-' + id;
    div.setAttribute('data-job-type', type);
    div.style.marginTop = '10px';

    function opt(value) {
        return '<option value="' + value + '"' +
            (value === type ? ' selected' : '') + '>' + value + '</option>';
    }

    div.innerHTML =
        '<div class="form-row" style="align-items:flex-end; gap:10px;">' +
        '<div class="form-group" style="flex:0 0 150px;">' +
        '<label class="form-label">Type</label>' +
        '<select class="form-input wo-group-type" onchange="_woGroupSetType(' + id + ')">' +
        opt('Internal') + opt('External') + opt('Design') +
        '</select>' +
        '</div>' +
        '<div class="wo-group-fields form-row" style="flex:1; gap:10px; margin:0;">' +
        _woGroupFieldsHtml(type) +
        '</div>' +
        '<div class="form-group" style="flex:0 0 40px;display:flex;align-items:flex-end;">' +
        '<button type="button" class="btn btn-danger" style="font-size:11px;padding:5px 8px;" onclick="removeWoJobGroup(' + id + ')"><i data-lucide="x" class="icon icon-sm"></i></button>' +
        '</div>' +
        '</div>';
    container.appendChild(div);
    refreshIcons(div);

    if (type === 'Internal') {
        loadMaterialsForLineItem(div.querySelector('.wo-material'));
    }
}

function _woGroupSetType(id) {
    var group = document.getElementById('woJobGroup-' + id);
    if (!group) return;
    var type = group.querySelector('.wo-group-type').value;
    group.setAttribute('data-job-type', type);
    var fields = group.querySelector('.wo-group-fields');
    fields.innerHTML = _woGroupFieldsHtml(type);
    refreshIcons(fields);
    if (type === 'Internal') {
        loadMaterialsForLineItem(fields.querySelector('.wo-material'));
    }
}

async function loadMaterialsForLineItem(selectEl) {
    if (!selectEl) return;
    try {
        var options = await apiGet('/api/inventory/options');
        var materials = options.materials || [];
        selectEl.innerHTML = '<option value="">Select...</option>' +
            materials.map(function(m) {
                return '<option value="' + escapeHtml(m) + '">' + escapeHtml(m) + '</option>';
            }).join('');
    } catch (e) {
        selectEl.innerHTML = '<option value="">Error</option>';
    }
}

function removeWoJobGroup(id) {
    var el = document.getElementById('woJobGroup-' + id);
    if (el) el.remove();
    // Ensure at least one group remains.
    if (document.querySelectorAll('.wo-job-group').length === 0) {
        addWoJobGroup('Internal');
    }
}

async function submitCreateWorkOrder() {
    var customer = document.getElementById('woCustomerName').value.trim();
    if (!customer) {
        showToast('Please enter a customer name', 'error');
        return;
    }

    var groups = document.querySelectorAll('.wo-job-group');
    var lineItems = [];
    var jobs = [];
    var error = null;

    groups.forEach(function(el) {
        if (error) return;
        var type = el.getAttribute('data-job-type') || 'Internal';

        if (type === 'Internal') {
            var partName = (el.querySelector('.wo-part-name').value || '').trim();
            var material = el.querySelector('.wo-material').value;
            var quantity = parseInt(el.querySelector('.wo-quantity').value) || 0;
            if (!partName || !material || quantity < 1) {
                error = 'Fill in Part Name, Material, and Qty for every Internal job.';
                return;
            }
            lineItems.push({
                part_name: partName,
                material: material,
                quantity: quantity
            });
        } else if (type === 'External') {
            var vendor = (el.querySelector('.wo-vendor').value || '').trim();
            var process = (el.querySelector('.wo-process').value || '').trim();
            if (!vendor || !process) {
                error = 'External jobs need both a Vendor and a Process.';
                return;
            }
            jobs.push({
                job_type: 'External',
                vendor: vendor,
                external_process: process
            });
        } else if (type === 'Design') {
            var designer = (el.querySelector('.wo-designer').value || '').trim();
            var requirements = (el.querySelector('.wo-requirements').value || '').trim();
            if (!designer) {
                error = 'Design jobs need a Designer.';
                return;
            }
            var spec = { job_type: 'Design', designer: designer };
            if (requirements) spec.requirements = requirements;
            jobs.push(spec);
        }
    });

    if (error) {
        showToast(error, 'error');
        return;
    }
    if (lineItems.length === 0 && jobs.length === 0) {
        showToast('Add at least one job', 'error');
        return;
    }

    var dueDateEl = document.getElementById('woDueDate');
    var dueDate = dueDateEl ? (dueDateEl.value || '').trim() : '';

    var payload = {
        customer_name: customer,
        line_items: lineItems,
        jobs: jobs
    };
    if (dueDate) payload.due_date = dueDate;

    try {
        var result = await apiPost('/api/workorders', payload);

        if (result.wo_id) {
            var partsCreated = result.parts_created;
            var jobCount = result.job_count || jobs.length;
            var msg = 'Created ' + result.wo_id;
            var bits = [];
            if (partsCreated) {
                bits.push(partsCreated + ' part' +
                    (partsCreated === 1 ? '' : 's') + ' queued');
            }
            if (jobCount) {
                bits.push(jobCount + ' job' + (jobCount === 1 ? '' : 's'));
            }
            if (bits.length) msg += ' — ' + bits.join(', ');
            showToast(msg);
            // Reset form
            document.getElementById('woCustomerName').value = '';
            if (dueDateEl) dueDateEl.value = '';
            document.getElementById('woJobGroups').innerHTML = '';
            _woLineItemCounter = 0;
            addWoJobGroup('Internal');
            // Jump to the new work order's detail page.
            window.location.href = '/work-orders/' +
                encodeURIComponent(result.wo_id) + '?from=all';
            return;
        } else {
            showToast('Error: ' + (result.error || 'Unknown'), 'error');
        }
    } catch (e) {
        showToast('Error: ' + e.message, 'error');
    }
}
