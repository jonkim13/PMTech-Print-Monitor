// ============================================================
// Production Log - Maintenance modal
// ============================================================

function showMaintenanceModal() {
    populatePrinterDropdowns();
    document.getElementById('maintNotes').value = '';
    document.getElementById('maintEventType').value = 'maintenance';
    showModal('maintenanceModal');
}

async function submitMaintenance() {
    const printerId = document.getElementById('maintPrinterSelect').value;
    if (!printerId) {
        showToast('Select a printer', 'error');

        return;
    }
    try {
        const resp = await fetch(`/api/production/machines/${encodeURIComponent(printerId)}/maintenance`, {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({
                event_type: document.getElementById('maintEventType').value,
                notes: document.getElementById('maintNotes').value,
            }),
        });
        const result = await resp.json();
        if(result.success) {
            showToast('Maintenance event logged');
            hideModal('maintenanceModal');
            loadMachineSummaries();
        } else {
            showToast(`Error: ${result.error}`, 'error');
        }
    } catch (e) {
        showToast(`Error: ${e.message}`, 'error');
    }
}
