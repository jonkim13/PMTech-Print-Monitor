// ============================================================
// Inventory - Filament table, filters, add/update/delete
// ============================================================
async function loadInventory() {
    const material = document.getElementById('invFilterMaterial').value;
    const brand = document.getElementById('invFilterBrand').value;
    const color = document.getElementById('invSearchColor').value;
    const supplier = document.getElementById('invFilterSupplier').value;

    let url = '/api/inventory?';
    if (material) url += `material=${encodeURIComponent(material)}&`;
    if (brand) url += `brand=${encodeURIComponent(brand)}&`;
    if (color) url += `color=${encodeURIComponent(color)}&`;
    if (supplier) url += `supplier=${encodeURIComponent(supplier)}&`;

    try {
        const resp = await fetch(url);
        const spools = await resp.json();

        const body = document.getElementById('inventoryBody');

        if(spools.length === 0) {
            body.innerHTML = '<tr><td colspan="11" class="table-empty">No spools found</td></tr>';

            return;
        }

        body.innerHTML = spools.map(s => {
            const spoolId = String(s.id || "");
            const spoolIdEnc = encodeURIComponent(spoolId);
            const grams = Number(s.grams) || 0;
            const diameter = Number(s.diameter);
            return `
            <tr>
                <td class="table-id">${escapeHtml(spoolId)}</td>
                <td>${escapeHtml(s.material || "")}</td>
                <td>${escapeHtml(s.brand || "")}</td>
                <td>${escapeHtml(s.color || "")}</td>
                <td>${escapeHtml(s.supplier || "")}</td>
                <td><span class="weight-badge ${getWeightClass(grams)}">${grams}g</span></td>
                <td>${Number.isFinite(diameter) ? diameter : "--"}mm</td>
                <td>${escapeHtml(s.operator || "")}</td>
                <td style="font-family: 'JetBrains Mono', monospace; font-size: 10px; color: var(--text-dim);">${escapeHtml(s.date_ins || "")}</td>
                <td style="font-family: 'JetBrains Mono', monospace; font-size: 10px; color: var(--text-dim);">${escapeHtml(formatDateTime(s.last_dried_at) || "--")}</td>
                <td>
                    <button class="btn" onclick="showUpdateWeight(decodeURIComponent('${spoolIdEnc}'), ${grams})" style="padding: 3px 6px; font-size: 9px;">Edit Weight</button>
                    <button class="btn btn-danger" onclick="deleteSpool(decodeURIComponent('${spoolIdEnc}'))" style="padding: 3px 6px; font-size: 9px;">Delete</button>
                </td>
            </tr>`;
        }).join('');
    } catch (e) {
        showToast(`Inventory error: ${e.message}`, 'error');
    }
}

async function loadInventoryOptions() {
    try {
        const resp = await fetch('/api/inventory/options');
        const opts = await resp.json();

        const matFilter = document.getElementById('invFilterMaterial');
        const matForm = document.getElementById('formMaterial');
        const brandFilter = document.getElementById('invFilterBrand');
        const supFilter = document.getElementById('invFilterSupplier');

        matFilter.innerHTML = '<option value="">All Materials</option>';
        matForm.innerHTML = '';
        brandFilter.innerHTML = '<option value="">All Brands</option>';
        supFilter.innerHTML = '<option value="">All Suppliers</option>';

        const filterMaterials = Array.from(
            new Set(opts.filter_materials || opts.materials || [])
        );
        const formMaterials = Array.from(
            new Set(opts.form_materials || opts.materials || [])
        );
        const brands = Array.from(new Set(opts.brands || []));
        const suppliers = Array.from(new Set(opts.suppliers || []));

        filterMaterials.forEach(m => {
            matFilter.add(new Option(m, m));
        });

        formMaterials.forEach(m => {
            matForm.add(new Option(m, m));
        });

        brands.forEach(b => {
            brandFilter.add(new Option(b, b));
        });

        suppliers.forEach(s => {
            supFilter.add(new Option(s, s));
        });
    } catch (e) {
        console.error('Failed to load inventory options:', e);
    }
}

function showAddFilamentModal() {
    hideModal('newSpoolIdModal');
    document.getElementById('addFilamentForm').reset();
    document.getElementById('formGrams').value = 1000;
    document.getElementById('formDiameter').value = 1.75;
    showModal('addFilamentModal');
}

function showNewSpoolIdModal(spoolId) {
    document.getElementById('newSpoolIdValue').textContent = spoolId;
    showModal('newSpoolIdModal');
}

async function submitAddFilament(e) {
    e.preventDefault();

    const data = {
        material: document.getElementById('formMaterial').value,
        brand: document.getElementById('formBrand').value,
        color: document.getElementById('formColor').value,
        supplier: document.getElementById('formSupplier').value,
        grams: parseInt(document.getElementById('formGrams').value),
        diameter: parseFloat(document.getElementById('formDiameter').value),
        batch: document.getElementById('formBatch').value,
        operator: document.getElementById('formOperator').value,
    };

    try {
        const resp = await fetch('/api/inventory', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify(data)
        });
        const result = await resp.json();

        if(result.success) {
            const spoolId = String(result.spool_id || result.id || '').trim();
            if(!spoolId) {
                showToast('Error: spool created but no spool ID was returned', 'error');
                return;
            }
            showToast(`Added spool: ${spoolId}`);
            hideModal('addFilamentModal');
            showNewSpoolIdModal(spoolId);
            loadInventory();
            await loadInventoryOptions();
        } else {
            showToast(`Error: ${result.error}`, 'error');
        }
    } catch (e) {
        showToast(`Error: ${e.message}`, 'error');
    }
}

function showUpdateWeight(spoolId, currentGrams) {
    document.getElementById('weightSpoolId').textContent = spoolId;
    document.getElementById('weightInput').value = currentGrams;
    document.getElementById('updateWeightModal').dataset.spoolId = spoolId;
    showModal('updateWeightModal');
}

async function submitUpdateWeight() {
    const spoolId = document.getElementById('updateWeightModal').dataset.spoolId;
    const newGrams = parseInt(document.getElementById('weightInput').value);

    try {
        const resp = await fetch(`/api/inventory/${spoolId}/weight`, {
            method: 'PUT',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ grams: newGrams })
        });
        const result = await resp.json();

        if(result.success) {
            showToast(`Updated weight for ${spoolId}`);
            hideModal('updateWeightModal');
            loadInventory();
        } else {
            showToast(`Error: ${result.error}`, 'error');
        }
    } catch (e) {
        showToast(`Error: ${e.message}`, 'error');
    }
}

async function deleteSpool(spoolId) {
    if(!confirm(`Delete spool ${spoolId}?`)) {
        return;
    }

    try {
        const resp = await fetch(`/api/inventory/${spoolId}`, { method: 'DELETE' });
        const result = await resp.json();

        if(result.success) {
            showToast(`Deleted spool ${spoolId}`);
            loadInventory();
        } else {
            showToast(`Error: ${result.error}`, 'error');
        }
    } catch (e) {
        showToast(`Error: ${e.message}`, 'error');
    }
}

// Load filter options on startup
loadInventoryOptions();
