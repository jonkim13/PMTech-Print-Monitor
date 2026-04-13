// ============================================================
// Production Log - Jobs list and job detail modal
// ============================================================

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
            <div class="job-detail-item"><span class="jd-label">Operator Initials</span><span class="jd-value">${escapeHtml(j.operator_initials || '-')}</span></div>
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
