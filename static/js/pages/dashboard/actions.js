// ============================================================
// Dashboard - Printer actions (stop print)
// ============================================================

async function stopPrint(printerId, printerName) {
    if(!confirm(`Stop the current print on ${printerName}?`)) {
        return;
    }

    try {
        const resp = await fetch(`/api/printers/${printerId}/stop`, { method: 'POST' });
        const result = await resp.json();

        if(result.success) {
            showToast(`Stopped print on ${printerName}`);
        } else {
            showToast(`Failed to stop: ${result.error || 'Unknown error'}`, 'error');
        }
    } catch (e) {
        showToast(`Error: ${e.message}`, 'error');
    }
}
