// ============================================================
// Production Log - Material traceability and CSV export
// ============================================================

async function loadSpoolUsage() {
    const spoolId = document.getElementById('materialSpoolSearch').value.trim();
    const tbody = document.getElementById('materialUsageBody');
    const summary = document.getElementById('materialSummary');

    if(!spoolId) {
        showToast('Enter a spool ID', 'error');
        return;
    }

    try {
        const resp = await fetch(`/api/production/materials/${encodeURIComponent(spoolId)}/usage`);
        const data = await resp.json();

        // Summary
        const t = data.totals || {};
        summary.innerHTML = `
        <div class="material-summary-bar">
            <div class="mat-stat"><span class="ms-label">Spool</span><span class="ms-value">${escapeHtml(spoolId)}</span></div>
            <div class="mat-stat"><span class="ms-label">Total Grams Used</span><span class="ms-value">${Number(t.total_grams || 0).toFixed(1)}g</span></div>
            <div class="mat-stat"><span class="ms-label">Total mm Used</span><span class="ms-value">${Number(t.total_mm || 0).toFixed(0)}mm</span></div>
            <div class="mat-stat"><span class="ms-label">Jobs</span><span class="ms-value">${t.job_count || 0}</span></div>
        </div>`;

        const usage = data.usage || [];
        if(usage.length === 0) {
            tbody.innerHTML = '<tr><td colspan="6" class="table-empty">No usage records found</td></tr>';

            return;
        }

        tbody.innerHTML = usage.map(u => `<tr>
            <td>${escapeHtml(formatDateTime(u.timestamp))}</td>
            <td>${u.job_id || '-'}</td>
            <td>${escapeHtml(u.printer_name || u.printer_id || '-')}</td>
            <td>${escapeHtml(u.file_display_name || u.file_name || '-')}</td>
            <td>${Number(u.grams_used || 0).toFixed(1)}</td>
            <td>${Number(u.mm_used || 0).toFixed(0)}</td>
        </tr>`).join('');
    } catch (e) {
        tbody.innerHTML = `<tr><td colspan="6" class="table-empty">Error: ${escapeHtml(e.message)}</td></tr>`;
        summary.innerHTML = '';
    }
}

function exportProductionCSV(type) {
    const dateFrom = document.getElementById('prodDateFrom').value;
    const dateTo = document.getElementById('prodDateTo').value;
    const params = new URLSearchParams();
    if(dateFrom) params.set('date_from', dateFrom + 'T00:00:00');
    if(dateTo) params.set('date_to', dateTo + 'T23:59:59');

    const urls = {
        jobs: `/api/production/export/jobs?${params}`,
        machines: `/api/production/export/machines?${params}`,
        materials: `/api/production/export/materials?${params}`,
    };

    const url = urls[type];
    if(url) {
        window.open(url, '_blank');
    }
}
