// ============================================================
// Production Log - ISO 9001 Traceability UI
// ============================================================
let _currentJobId = null;

// ---- Sub-tab Navigation ----
function switchProdTab(tab) {
    document.querySelectorAll('.prod-tab').forEach(t => t.classList.remove('active'));
    document.querySelector(`[data-prodtab="${tab}"]`).classList.add('active');
    document.querySelectorAll('.prod-panel').forEach(p => p.classList.remove('active'));
    document.getElementById(`prodPanel-${tab}`).classList.add('active');

    if (tab === 'jobs') loadProductionJobs();
    if (tab === 'machines') loadMachineSummaries();
    if (tab === 'materials') { /* user searches manually */ }
}

// ---- Main loader called from switchPage ----
function loadProductionData() {
    populatePrinterDropdowns();
    loadProductionJobs();
}

function populatePrinterDropdowns() {
    const selectors = ['prodFilterPrinter', 'machineLogPrinter', 'maintPrinterSelect'];

    for(const id of selectors) {
        const el = document.getElementById(id);
        if(!el) continue;
        // Keep existing first option
        const firstOpt = el.options[0] ? el.options[0].outerHTML : '';
        el.innerHTML = firstOpt;

        for (const p of printerList) {
            const opt = document.createElement('option');
            opt.value = p.printer_id;
            opt.textContent = p.name || p.printer_id;
            el.appendChild(opt);
        }
    }
}

// ---- Production Jobs ----
async function loadProductionJobs() {
    const params = new URLSearchParams();
    const printer = document.getElementById('prodFilterPrinter').value;
    const status = document.getElementById('prodFilterStatus').value;
    const outcome = document.getElementById('prodFilterOutcome').value;
    const dateFrom = document.getElementById('prodDateFrom').value;
    const dateTo = document.getElementById('prodDateTo').value;

    if (printer) params.set('printer_id', printer);
    if (status) params.set('status', status);
    if (outcome) params.set('outcome', outcome);
    if (dateFrom) params.set('date_from', dateFrom + 'T00:00:00');
    if (dateTo) params.set('date_to', dateTo + 'T23:59:59');

    try {
        const resp = await fetch(`/api/production/jobs?${params}`);
        const jobs = await resp.json();
        const tbody = document.getElementById('prodJobsBody');

        if (jobs.length === 0) {
            tbody.innerHTML = '<tr><td colspan="12" class="table-empty">No jobs found</td></tr>';
            return;
        }

        tbody.innerHTML = jobs.map(j => {
            const statusClass = j.status === 'completed' ? 'badge-finished'
                : j.status === 'failed' ? 'badge-error'
                : j.status === 'stopped' ? 'badge-offline'
                : 'badge-printing';
            const outcomeClass = j.outcome === 'pass' ? 'outcome-pass'
                : j.outcome === 'fail' ? 'outcome-fail' : 'outcome-unknown';

            return `<tr class="prod-row" onclick="showJobDetail(${j.job_id})">
                <td>${j.job_id}</td>
                <td>${escapeHtml(formatDateTime(j.started_at))}</td>
                <td>${escapeHtml(j.printer_name)}</td>
                <td title="${escapeHtml(j.file_display_name || j.file_name || '')}">${escapeHtml(truncate(j.file_display_name || j.file_name || '', 25))}</td>
                <td>${escapeHtml(j.spool_material || j.filament_type || '-')}</td>
                <td>${escapeHtml(j.spool_id || '-')}</td>
                <td>${formatTime(j.print_duration_sec)}</td>
                <td>${j.filament_used_g ? Number(j.filament_used_g).toFixed(1) : '-'}</td>
                <td><span class="printer-status-badge ${statusClass}">${escapeHtml(j.status)}</span></td>
                <td><span class="outcome-badge ${outcomeClass}">${escapeHtml(j.outcome)}</span></td>
                <td>${escapeHtml(j.operator || '-')}</td>
                <td><button class="btn btn-sm" onclick="event.stopPropagation(); showJobDetail(${j.job_id})">View</button></td>
            </tr>`;
        }).join('');
    } catch (e) {
        document.getElementById('prodJobsBody').innerHTML =
            `<tr><td colspan="12" class="table-empty">Error: ${escapeHtml(e.message)}</td></tr>`;
    }
}

function truncate(str, len) {
    if (str.length <= len) return str;
    return str.substring(0, len) + '...';
}

// ---- Job Detail Modal ----
async function showJobDetail(jobId) {
    _currentJobId = jobId;
    document.getElementById('jobDetailId').textContent = `#${jobId}`;
    document.getElementById('jobDetailContent').innerHTML = '<div class="events-empty">Loading...</div>';
    showModal('jobDetailModal');

    try {
        const resp = await fetch(`/api/production/jobs/${jobId}`);
        const j = await resp.json();

        if(j.error) {
            document.getElementById('jobDetailContent').innerHTML =
                `<div class="events-empty">${escapeHtml(j.error)}</div>`;
            return;
        }

        let snapshotHTML = '';
        if(j.snapshot_path) {
            snapshotHTML = `
            <div class="job-snapshot">
                <img src="/api/production/jobs/${jobId}/snapshot"
                     alt="Print snapshot" style="max-width: 100%; border-radius: 8px; margin-top: 8px;">
            </div>`;
        }

        document.getElementById('jobDetailContent').innerHTML = `
        <div class="job-detail-grid">
            <div class="job-detail-item"><span class="jd-label">Printer</span><span class="jd-value">${escapeHtml(j.printer_name)}</span></div>
            <div class="job-detail-item"><span class="jd-label">File</span><span class="jd-value">${escapeHtml(j.file_display_name || j.file_name || '-')}</span></div>
            <div class="job-detail-item"><span class="jd-label">Status</span><span class="jd-value">${escapeHtml(j.status)}</span></div>
            <div class="job-detail-item"><span class="jd-label">Started</span><span class="jd-value">${escapeHtml(formatDateTime(j.started_at))}</span></div>
            <div class="job-detail-item"><span class="jd-label">Completed</span><span class="jd-value">${j.completed_at ? escapeHtml(formatDateTime(j.completed_at)) : '-'}</span></div>
            <div class="job-detail-item"><span class="jd-label">Duration</span><span class="jd-value">${formatTime(j.print_duration_sec)}</span></div>
            <div class="job-detail-item"><span class="jd-label">Material</span><span class="jd-value">${escapeHtml(j.spool_material || j.filament_type || '-')}</span></div>
            <div class="job-detail-item"><span class="jd-label">Spool ID</span><span class="jd-value">${escapeHtml(j.spool_id || '-')}</span></div>
            <div class="job-detail-item"><span class="jd-label">Spool Brand</span><span class="jd-value">${escapeHtml(j.spool_brand || '-')}</span></div>
            <div class="job-detail-item"><span class="jd-label">Filament Used</span><span class="jd-value">${j.filament_used_g ? Number(j.filament_used_g).toFixed(1) + 'g' : '-'}</span></div>
            <div class="job-detail-item"><span class="jd-label">Layer Height</span><span class="jd-value">${j.layer_height ? j.layer_height + 'mm' : '-'}</span></div>
            <div class="job-detail-item"><span class="jd-label">Nozzle Diameter</span><span class="jd-value">${j.nozzle_diameter ? j.nozzle_diameter + 'mm' : '-'}</span></div>
            <div class="job-detail-item"><span class="jd-label">Nozzle Temp</span><span class="jd-value">${j.nozzle_temp ? j.nozzle_temp + '°C' : '-'}</span></div>
            <div class="job-detail-item"><span class="jd-label">Bed Temp</span><span class="jd-value">${j.bed_temp ? j.bed_temp + '°C' : '-'}</span></div>
            <div class="job-detail-item"><span class="jd-label">Fill Density</span><span class="jd-value">${j.fill_density ? j.fill_density + '%' : '-'}</span></div>
        </div>
        ${snapshotHTML}`;

        // Populate QC fields
        document.getElementById('jobOutcomeSelect').value = j.outcome || 'unknown';
        document.getElementById('jobOperatorInput').value = j.operator || '';
        document.getElementById('jobNotesInput').value = j.notes || '';

    } catch (e) {
        document.getElementById('jobDetailContent').innerHTML =
            `<div class="events-empty">Error: ${escapeHtml(e.message)}</div>`;
    }
}

async function saveJobQC() {
    if(!_currentJobId) return;

    try {
        const resp = await fetch(`/api/production/jobs/${_currentJobId}`, {
            method: 'PATCH',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({
                outcome: document.getElementById('jobOutcomeSelect').value,
                operator: document.getElementById('jobOperatorInput').value,
                notes: document.getElementById('jobNotesInput').value,
            }),
        });

        const result = await resp.json();
        if(result.success) {
            showToast('QC data saved');
            hideModal('jobDetailModal');
            loadProductionJobs();
        } else {
            showToast(`Error: ${result.error}`, 'error');
        }
    } catch (e) {
        showToast(`Error: ${e.message}`, 'error');
    }
}

// ---- Machine Summary ----

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

// ---- Maintenance Modal ----

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

// ---- Material Traceability ----

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

// ---- CSV Export ----

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
