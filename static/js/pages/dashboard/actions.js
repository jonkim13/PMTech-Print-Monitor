// ============================================================
// Dashboard - Printer actions (stop print with confirm modal)
// ============================================================

function stopPrint(printerId, printerName) {
    const titleEl = document.getElementById('stopPrintConfirmTitle');
    const btnEl = document.getElementById('stopPrintConfirmBtn');
    if (!titleEl || !btnEl) {
        // Defensive — if modal wasn't included, fall back to native confirm.
        if (window.confirm('Stop the current print on ' + printerName + '?')) {
            _stopPrintCommit(printerId, printerName);
        }
        return;
    }
    titleEl.textContent = 'Stop print on ' + printerName + '?';
    btnEl.onclick = function () {
        hideModal('stopPrintConfirmModal');
        _stopPrintCommit(printerId, printerName);
    };
    showModal('stopPrintConfirmModal');
}

async function _stopPrintCommit(printerId, printerName) {
    try {
        const resp = await fetch('/api/printers/' + encodeURIComponent(printerId) + '/stop', { method: 'POST' });
        const result = await resp.json();
        if (result.success) {
            showToast('Stopped print on ' + printerName);
        } else {
            showToast('Failed to stop: ' + (result.error || 'Unknown error'), 'error');
        }
    } catch (e) {
        showToast('Error: ' + e.message, 'error');
    }
}
