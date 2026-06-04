// ============================================================
// Work Orders - Create form: typed job groups, submission.
//
// Phase G — the New WO page builds a whole order in one submit. Each
// "job group" is a bordered card with a type-agnostic header (numbered
// badge + type selector + remove) and a divider above the type-specific
// fields. Switching the type swaps ONLY the fields below the divider —
// the card chrome stays identical for Internal/External/Design.
//   Internal → Part Name + Material + Qty stepper  (→ line_items)
//   External → Vendor + Process                    (→ a job row)
//   Design   → Designer + Requirements             (→ a job row)
// On submit the groups are split into `line_items` (Internal) + `jobs`
// (External/Design) and POSTed to /api/workorders, which creates
// everything atomically. The payload shape is unchanged.
// (_woLineItemCounter, declared in index.js, is a monotonic element id —
// NOT the displayed position; badges are renumbered by DOM order.)
// ============================================================

function initCreateForm() {
    if (document.querySelectorAll('.wo-job-group').length === 0) {
        _woLineItemCounter = 0;
        addWoJobGroup('Internal');
    }
    _woAfterChange();
}

// Renumber badges by DOM position, refresh the header/footer job counts,
// and toggle the empty-state placeholder. Called after every add/remove/
// reset so 01/02/03 and the counts stay correct (e.g. after a mid-list
// removal the survivors renumber).
function _woAfterChange() {
    var groups = document.querySelectorAll('.wo-job-group');
    groups.forEach(function (g, i) {
        var badge = g.querySelector('[data-job-badge]');
        if (badge) badge.textContent = ('0' + (i + 1)).slice(-2);
    });
    var n = groups.length;
    var label = n + ' job' + (n === 1 ? '' : 's');
    var head = document.getElementById('woJobsHeadCount');
    if (head) head.textContent = '· ' + label;
    var foot = document.getElementById('woFooterCount');
    if (foot) foot.textContent = label;
    _woRefreshEmptyState();
}

function _woRefreshEmptyState() {
    var empty = document.getElementById('woJobGroupsEmpty');
    if (!empty) return;
    empty.hidden = document.querySelectorAll('.wo-job-group').length > 0;
}

function _woGroupFieldsHtml(type) {
    if (type === 'External') {
        return '' +
            '<div class="form-group" style="flex:1 1 200px;">' +
            '<label class="form-label wo-label">Vendor</label>' +
            '<input type="text" class="form-input wo-vendor" maxlength="120" placeholder="e.g. MachiningCo">' +
            '</div>' +
            '<div class="form-group" style="flex:1 1 200px;">' +
            '<label class="form-label wo-label">Process</label>' +
            '<input type="text" class="form-input wo-process" maxlength="120" placeholder="e.g. CNC Mill / Anodize">' +
            '</div>';
    }
    if (type === 'Design') {
        return '' +
            '<div class="form-group" style="flex:1 1 200px;">' +
            '<label class="form-label wo-label">Designer</label>' +
            '<input type="text" class="form-input wo-designer" maxlength="120" placeholder="Designer name">' +
            '</div>' +
            '<div class="form-group" style="flex:2 1 260px;">' +
            '<label class="form-label wo-label">Requirements (optional)</label>' +
            '<textarea class="form-input wo-requirements" rows="2" placeholder="Optional"></textarea>' +
            '</div>';
    }
    // Internal (default) — Part Name, Material, Qty stepper.
    return '' +
        '<div class="form-group" style="flex:2 1 220px;">' +
        '<label class="form-label wo-label">Part Name</label>' +
        '<input type="text" class="form-input wo-part-name" placeholder="e.g. Widget Bracket">' +
        '</div>' +
        '<div class="form-group" style="flex:1 1 150px;">' +
        '<label class="form-label wo-label">Material</label>' +
        '<select class="form-input wo-material"><option value="">Select...</option></select>' +
        '</div>' +
        '<div class="form-group" style="flex:0 0 auto;">' +
        '<label class="form-label wo-label">Qty</label>' +
        '<div class="wo-qty-stepper">' +
        '<button type="button" class="wo-qty-btn wo-qty-btn-minus" onclick="_woQtyStep(this, -1)" aria-label="Decrease quantity">−</button>' +
        '<input type="number" class="wo-qty-value wo-quantity" min="1" value="1" readonly>' +
        '<button type="button" class="wo-qty-btn wo-qty-btn-plus" onclick="_woQtyStep(this, 1)" aria-label="Increase quantity">+</button>' +
        '</div>' +
        '</div>';
}

function _woQtyStep(btn, delta) {
    var input = btn.parentNode.querySelector('.wo-quantity');
    if (!input) return;
    var v = (parseInt(input.value, 10) || 1) + delta;
    if (v < 1) v = 1;
    input.value = v;
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

    function opt(value) {
        return '<option value="' + value + '"' +
            (value === type ? ' selected' : '') + '>' + value + '</option>';
    }

    // Type-agnostic chrome (badge + type select + remove + divider) built
    // once; only .wo-job-fields differs by type.
    div.innerHTML =
        '<div class="wo-job-group-head">' +
        '<span class="wo-job-badge" data-job-badge>01</span>' +
        '<select class="form-input wo-job-type-select" aria-label="Job type" onchange="_woGroupSetType(' + id + ')">' +
        opt('Internal') + opt('External') + opt('Design') +
        '</select>' +
        '<button type="button" class="btn btn-danger btn-sm wo-job-remove" onclick="removeWoJobGroup(' + id + ')" aria-label="Remove job"><i data-lucide="x" class="icon icon-sm"></i></button>' +
        '</div>' +
        '<div class="wo-job-divider"></div>' +
        '<div class="wo-job-fields">' +
        _woGroupFieldsHtml(type) +
        '</div>';
    container.appendChild(div);
    refreshIcons(div);

    if (type === 'Internal') {
        loadMaterialsForLineItem(div.querySelector('.wo-material'));
    }
    _woAfterChange();
}

function _woGroupSetType(id) {
    var group = document.getElementById('woJobGroup-' + id);
    if (!group) return;
    var type = group.querySelector('.wo-job-type-select').value;
    group.setAttribute('data-job-type', type);
    // Swap ONLY the fields below the divider — chrome is untouched.
    var fields = group.querySelector('.wo-job-fields');
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

// Remove this group entirely (no auto-re-add). Removing the last group
// lands on the empty-state placeholder; "Create Work Order" rejects a
// zero-group form client-side below.
function removeWoJobGroup(id) {
    var el = document.getElementById('woJobGroup-' + id);
    if (el) el.remove();
    _woAfterChange();
}

// Footer Reset — clear the whole form back to one empty Internal group.
// Distinct from the per-card X (which removes a single card).
function resetCreateForm() {
    var c = document.getElementById('woCustomerName');
    if (c) c.value = '';
    var due = document.getElementById('woDueDate');
    if (due) due.value = '';
    document.getElementById('woJobGroups').innerHTML = '';
    _woLineItemCounter = 0;
    addWoJobGroup('Internal');
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
            var quantity = parseInt(el.querySelector('.wo-quantity').value, 10) || 0;
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
            // Reset the form, then jump to the new WO's detail page.
            resetCreateForm();
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
