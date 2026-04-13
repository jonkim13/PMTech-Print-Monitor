// ============================================================
// Work Orders - Create form, line items, submission
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
