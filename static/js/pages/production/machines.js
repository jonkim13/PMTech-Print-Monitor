// ============================================================
// Production Log - Machine summaries and machine log
// ============================================================

async function loadMachineSummaries() {
    try {
        const resp = await fetch('/api/production/machines');
        const machines = await resp.json();
        const container = document.getElementById('prodMachineCards');

        if(machines.length === 0) {
            container.innerHTML = '<div class="events-empty">No machine data yet</div>';
            return;
        }

        container.innerHTML = machines.map(m => `
        <div class="machine-card">
            <div class="machine-header">${escapeHtml(m.printer_name || m.printer_id)}</div>
            <div class="machine-stats">
                <div class="machine-stat">
                    <span class="ms-label">Total Jobs</span>
                    <span class="ms-value">${m.total_jobs}</span>
                </div>
                <div class="machine-stat">
                    <span class="ms-label">Completed</span>
                    <span class="ms-value green">${m.completed}</span>
                </div>
                <div class="machine-stat">
                    <span class="ms-label">Failed</span>
                    <span class="ms-value red">${m.failed}</span>
                </div>
                <div class="machine-stat">
                    <span class="ms-label">Success Rate</span>
                    <span class="ms-value blue">${m.success_rate}%</span>
                </div>
                <div class="machine-stat">
                    <span class="ms-label">Print Hours</span>
                    <span class="ms-value">${m.total_print_hours}h</span>
                </div>
                <div class="machine-stat">
                    <span class="ms-label">Streak</span>
                    <span class="ms-value orange">${m.current_streak}</span>
                </div>
            </div>
            <div class="machine-maint">
                Last Maintenance: ${m.last_maintenance ? escapeHtml(formatDateTime(m.last_maintenance)) : 'Never'}
            </div>
        </div>`).join('');
    } catch (e) {
        document.getElementById('prodMachineCards').innerHTML =
            `<div class="events-empty">Error: ${escapeHtml(e.message)}</div>`;
    }
}

async function loadMachineLog() {
    const printerId = document.getElementById('machineLogPrinter').value;
    const tbody = document.getElementById('machineLogBody');

    if(!printerId) {
        tbody.innerHTML = '<tr><td colspan="5" class="table-empty">Select a printer to view log</td></tr>';
        return;
    }
    try {
        const resp = await fetch(`/api/production/machines/${encodeURIComponent(printerId)}/log`);
        const logs = await resp.json();
        if(logs.length === 0) {
            tbody.innerHTML = '<tr><td colspan="5" class="table-empty">No events logged</td></tr>';
            return;
        }
        tbody.innerHTML = logs.map(l => {
            let details = '';
            try { details = JSON.stringify(JSON.parse(l.details || '{}')); } catch (_) { details = l.details || ''; }
            if(details === '{}') details = '';
            return `<tr>
                <td>${escapeHtml(formatDateTime(l.event_timestamp))}</td>
                <td>${escapeHtml(l.printer_name)}</td>
                <td><span class="event-type-badge">${escapeHtml(l.event_type)}</span></td>
                <td class="details-cell" title="${escapeHtml(details)}">${escapeHtml(truncate(details, 40))}</td>
                <td>${l.total_print_hours_at_event}h</td>
            </tr>`;
        }).join('');
    } catch (e) {
        tbody.innerHTML = `<tr><td colspan="5" class="table-empty">Error: ${escapeHtml(e.message)}</td></tr>`;
    }
}
