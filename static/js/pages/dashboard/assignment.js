// ============================================================
// Dashboard - Spool assignment (multi-tool aware)
// ============================================================

function getAssignedSpoolIdForTool(printer, toolIndex) {
    if(!printer) {
        return '';
    }

    const normalizedToolIndex = Number(toolIndex) || 0;
    const toolCount = Number(printer.tool_count) || 1;
    if(toolCount > 1) {
        const entry = (printer.assigned_spools || []).find(function(assignment) {
            return Number(assignment.tool_index) === normalizedToolIndex;
        });
        return (entry && entry.spool && entry.spool.id) ? String(entry.spool.id) : '';
    }

    return (normalizedToolIndex === 0 && printer.assigned_spool && printer.assigned_spool.id)
        ? String(printer.assigned_spool.id)
        : '';
}

function renderAssignSpoolOptions() {
    const modal = document.getElementById('assignSpoolModal');
    const select = document.getElementById('assignSpoolSelect');
    const inventorySpools = Array.isArray(modal._inventorySpools) ? modal._inventorySpools : [];

    if(inventorySpools.length === 0) {
        select.innerHTML = '<option value="">No spools in inventory</option>';
        return;
    }

    const printerId = modal.dataset.printerId;
    const toolIndex = parseInt(document.getElementById('assignToolSelect').value, 10) || 0;
    const printer = (printerList || []).find(function(item) {
        return item.printer_id === printerId;
    });
    const currentSpoolId = getAssignedSpoolIdForTool(printer, toolIndex);
    const assignedSpoolIds = new Set();

    (printerList || []).forEach(function(item) {
        const itemToolCount = Number(item.tool_count) || 1;
        if(itemToolCount > 1) {
            (item.assigned_spools || []).forEach(function(assignment) {
                const spool = assignment.spool;
                if(spool && spool.id) {
                    assignedSpoolIds.add(String(spool.id));
                }
            });
            return;
        }

        if(item.assigned_spool && item.assigned_spool.id) {
            assignedSpoolIds.add(String(item.assigned_spool.id));
        }
    });

    const availableSpools = inventorySpools.filter(function(spool) {
        const spoolId = String(spool.id || '');
        return spoolId && (!assignedSpoolIds.has(spoolId) || spoolId === currentSpoolId);
    });

    if(availableSpools.length === 0) {
        select.innerHTML = '<option value="">No available spools</option>';
        return;
    }

    select.innerHTML = '<option value="">-- Select a spool --</option>' +
        availableSpools.map(function(spool) {
            const spoolId = String(spool.id || '');
            const label = escapeHtml(spoolId) + ' - '
                + escapeHtml(spool.material || '') + ' '
                + escapeHtml(spool.color || '') + ' ('
                + escapeHtml(String(spool.grams || 0)) + 'g)';
            return '<option value="' + escapeHtml(spoolId) + '">' + label + '</option>';
        }).join('');

    select.value = currentSpoolId || '';
}

async function showAssignSpoolModal(printerId, printerName) {
    document.getElementById('assignPrinterName').textContent = printerName;
    const modal = document.getElementById('assignSpoolModal');
    modal.dataset.printerId = printerId;
    modal._inventorySpools = [];
    document.getElementById('assignWasDried').checked = false;

    // Find the printer to determine tool count
    const printer = (printerList || []).find(p => p.printer_id === printerId);
    const toolCount = (printer && printer.tool_count) ? printer.tool_count : 1;
    modal.dataset.toolCount = toolCount;

    // Show/hide tool selector based on multi-tool
    const toolGroup = document.getElementById('assignToolGroup');
    const toolSelect = document.getElementById('assignToolSelect');
    if(toolCount > 1) {
        toolGroup.style.display = '';
        toolSelect.innerHTML = '';
        for(let t = 0; t < toolCount; t++) {
            toolSelect.innerHTML += `<option value="${t}">Tool ${t + 1} (T${t + 1})</option>`;
        }
    } else {
        toolGroup.style.display = 'none';
        toolSelect.innerHTML = '<option value="0">Tool 1</option>';
    }
    toolSelect.onchange = renderAssignSpoolOptions;

    const select = document.getElementById('assignSpoolSelect');
    select.innerHTML = '<option value="">Loading spools...</option>';
    showModal('assignSpoolModal');

    try {
        const resp = await fetch('/api/inventory');
        const spools = await resp.json();

        if(!Array.isArray(spools)) {
            select.innerHTML = '<option value="">Error loading spools</option>';
            return;
        }

        if(spools.length === 0) {
            select.innerHTML = '<option value="">No spools in inventory</option>';
            return;
        }

        modal._inventorySpools = spools;
        renderAssignSpoolOptions();
    } catch (e) {
        select.innerHTML = '<option value="">Error loading spools</option>';
    }
}

async function submitAssignSpool() {
    var printerId = document.getElementById('assignSpoolModal').dataset.printerId;
    var spoolId = document.getElementById('assignSpoolSelect').value;
    var toolIndex = parseInt(document.getElementById('assignToolSelect').value) || 0;
    var wasDried = document.getElementById('assignWasDried').checked;

    if(!spoolId) {
        showToast('Please select a spool', 'error');
        return;
    }

    try {
        var resp = await fetch('/api/assignments/' + printerId, {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({
                spool_id: spoolId,
                tool_index: toolIndex,
                was_dried: wasDried
            })
        });

        var result = await resp.json();

        if(result.success) {
            var toolLabel = (parseInt(document.getElementById('assignSpoolModal').dataset.toolCount) > 1)
                ? ' to T' + (toolIndex + 1) : '';
            showToast('Assigned spool ' + spoolId + toolLabel);
            hideModal('assignSpoolModal');
            poll();
        } else {
            showToast(result.error || 'Assignment failed', 'error');
        }
    } catch (e) {
        showToast('Error: ' + e.message, 'error');
    }
}

async function unassignSpool() {
    var printerId = document.getElementById('assignSpoolModal').dataset.printerId;
    var toolCount = parseInt(document.getElementById('assignSpoolModal').dataset.toolCount) || 1;
    var toolIndex = parseInt(document.getElementById('assignToolSelect').value) || 0;

    // For multi-tool, unassign just the selected tool
    var url = '/api/assignments/' + printerId + '?tool_index=' + toolIndex;

    try {
        var resp = await fetch(url, { method: 'DELETE' });
        var result = await resp.json();

        if(result.success) {
            var label = (toolCount > 1) ? 'T' + (toolIndex + 1) + ' spool' : 'Spool';
            showToast(label + ' unassigned');
            hideModal('assignSpoolModal');
            poll();
        } else {
            showToast('No spool was assigned', 'error');
        }
    } catch (e) {
        showToast('Error: ' + e.message, 'error');
    }
}
