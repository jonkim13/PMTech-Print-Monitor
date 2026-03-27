// ============================================================
// Dashboard - Printer cards, stats, events, poll loop
// ============================================================
function renderCard(printer) {
    const rawStatus = String(printer.status || "unknown").toLowerCase();
    const status = rawStatus.replace(/[^a-z_]/g, "") || "unknown";
    const job = printer.job || {};
    const temps = printer.temperatures || {};
    const isPrinting = status === "printing";
    const isFinished = status === "finished";
    const hasJob = isPrinting || isFinished;
    const progress = Number(job.progress) || 0;
    const progressClamped = Math.max(0, Math.min(100, progress));
    const pid = String(printer.printer_id || "");
    const printerName = String(printer.name || "");
    const printerModel = String(printer.model || "");
    const pidEnc = encodeURIComponent(pid);
    const nameEnc = encodeURIComponent(printerName);
    const safeFilename = escapeHtml(job.filename || "Unknown file");

    // Filament spool assignment info (multi-tool aware)
    const toolCount = Number(printer.tool_count) || 1;
    const assignedSpools = printer.assigned_spools || [];
    let spoolHTML = '';

    if(toolCount > 1) {
        // Multi-tool printer (XL) — show per-tool assignments
        let toolRows = '';
        for(let t = 0; t < toolCount; t++) {
            const entry = assignedSpools.find(a => a.tool_index === t);
            const spool = entry ? entry.spool : null;
            if(spool) {
                const w = Number(spool.grams) || 0;
                const wc = getWeightClass(w);
                toolRows += `<div class="spool-detail" style="margin-bottom:2px;">
                    <span class="spool-tool-label">T${t + 1}:</span>
                    <span class="spool-id">${escapeHtml(spool.id)}</span>
                    <span>${escapeHtml(spool.material)} · ${escapeHtml(spool.color)}</span>
                    <span class="weight-badge ${wc}">${w}g</span>
                </div>`;
            } else {
                toolRows += `<div class="spool-detail" style="margin-bottom:2px;">
                    <span class="spool-tool-label">T${t + 1}:</span>
                    <span class="spool-none">empty</span>
                </div>`;
            }
        }
        spoolHTML = `<div class="spool-info">
            <span class="spool-label">Tool Spools</span>
            ${toolRows}
        </div>`;
    } else {
        // Single-tool printer (Core One)
        const spool = printer.assigned_spool;
        if(spool) {
            const spoolWeight = Number(spool.grams) || 0;
            const weightClass = getWeightClass(spoolWeight);
            spoolHTML = `
            <div class="spool-info">
                <span class="spool-label">Loaded Spool</span>
                <div class="spool-detail">
                    <span class="spool-id">${escapeHtml(spool.id)}</span>
                    <span>${escapeHtml(spool.material)} · ${escapeHtml(spool.color)} · ${escapeHtml(spool.brand)}</span>
                    <span class="weight-badge ${weightClass}">${spoolWeight}g</span>
                </div>
            </div>`;
        } else {
            spoolHTML = `<div class="spool-info"><span class="spool-none">No spool assigned</span></div>`;
        }
    }

    let progressHTML = '';
    if(hasJob) {
        progressHTML = `
        <div class="progress-section">
            <div class="progress-header">
                <span class="progress-filename" title="${safeFilename}">${safeFilename}</span>
                <span class="progress-percent ${isPrinting ? 'printing-pulse' : ''}">${progressClamped.toFixed(1)}%</span>
            </div>
            <div class="progress-bar-track">
                <div class="progress-bar-fill ${isFinished ? 'complete' : ''}" style="width: ${progressClamped}%"></div>
            </div>
            <div class="progress-time">
                <span>Elapsed: ${formatTime(job.time_elapsed_sec)}</span>
                <span>Remaining: ${formatTime(job.time_remaining_sec)}</span>
            </div>
            ${isPrinting && job.time_remaining_sec > 0 ? `<div class="progress-eta">${formatETA(job.time_remaining_sec)}</div>` : ''}
        </div>`;

    } else {
        progressHTML = '<div class="no-job">No active print job</div>';
    }

    return `
    <div class="printer-card status-${status}">
        <div class="card-header">
            <div>
                <div class="printer-name">${escapeHtml(printerName)}</div>
                <div class="printer-model">${escapeHtml(printerModel)}</div>
            </div>
            <span class="printer-status-badge ${getBadgeClass(status)}">${escapeHtml(rawStatus)}</span>
        </div>
        ${spoolHTML}
        ${progressHTML}
        <div class="temps">
            <div class="temp-item">
                <span class="temp-label">Nozzle</span>
                <span class="temp-value">
                    ${(temps.nozzle_current || 0).toFixed(0)}&deg;C
                    <span class="target">/ ${(temps.nozzle_target || 0).toFixed(0)}&deg;C</span>
                </span>
            </div>
            <div class="temp-item">
                <span class="temp-label">Bed</span>
                <span class="temp-value">
                    ${(temps.bed_current || 0).toFixed(0)}&deg;C
                    <span class="target">/ ${(temps.bed_target || 0).toFixed(0)}&deg;C</span>
                </span>
            </div>
        </div>
        <div class="card-actions">
            <button class="btn btn-primary" onclick="showUploadModal(decodeURIComponent('${pidEnc}'), decodeURIComponent('${nameEnc}'))">Send GCode</button>
            <button class="btn btn-green" onclick="showFilesModal(decodeURIComponent('${pidEnc}'), decodeURIComponent('${nameEnc}'))">Files</button>
            <button class="btn btn-orange" onclick="showAssignSpoolModal(decodeURIComponent('${pidEnc}'), decodeURIComponent('${nameEnc}'))">Assign Spool</button>
            <button class="btn btn-danger" onclick="stopPrint(decodeURIComponent('${pidEnc}'), decodeURIComponent('${nameEnc}'))" ${isPrinting ? '' : 'disabled'}>Stop Print</button>
        </div>
    </div>`;
}

function renderEvent(event) {
    const dotClass = event.type === "print_complete" ? "complete"
        : event.type === "printer_error" ? "error" : "started";

    let text = "";
    if(event.type === "print_complete") {
        text = `<strong>${escapeHtml(event.printer_name)}</strong> finished printing ${escapeHtml(event.filename || 'a job')}`;
    } else if(event.type === "print_started") {
        text = `<strong>${escapeHtml(event.printer_name)}</strong> started printing ${escapeHtml(event.filename || 'a job')}`;
    } else if(event.type === "printer_error") {
        text = `<strong>${escapeHtml(event.printer_name)}</strong> encountered an error`;
    } else {
        text = `<strong>${escapeHtml(event.printer_name || '')}</strong>: ${escapeHtml(event.from_status)} → ${escapeHtml(event.to_status)}`;
    }

    return `
    <div class="event-item">
        <span class="event-time">${formatTimestamp(event.timestamp)}</span>
        <div class="event-dot ${dotClass}"></div>
        <span class="event-text">${text}</span>
    </div>`;
}

function updateStats(printers) {
    const total = printers.length;
    const printing = printers.filter(p => p.status === "printing").length;
    const idle = printers.filter(p => p.status === "idle").length;
    const finished = printers.filter(p => p.status === "finished").length;
    const errors = printers.filter(p => p.status === "error" || p.status === "offline").length;

    document.getElementById("statTotal").textContent = total;
    document.getElementById("statPrinting").textContent = printing;
    document.getElementById("statIdle").textContent = idle;
    document.getElementById("statFinished").textContent = finished;
    document.getElementById("statErrors").textContent = errors;
}

// ============================================================
// Printer Actions
// ============================================================
function showUploadModal(printerId, printerName) {
    document.getElementById('uploadPrinterName').textContent = printerName;
    document.getElementById('uploadModal').dataset.printerId = printerId;
    document.getElementById('gcodeFile').value = '';
    document.getElementById('printAfterUpload').checked = false;
    document.getElementById('uploadOperatorInitials').value = '';
    toggleUploadOperatorInitials();
    showModal('uploadModal');
}

function toggleUploadOperatorInitials() {
    const printAfter = document.getElementById('printAfterUpload').checked;
    const group = document.getElementById('uploadOperatorGroup');
    const input = document.getElementById('uploadOperatorInitials');

    group.style.display = printAfter ? 'block' : 'none';
    input.disabled = !printAfter;
    input.required = printAfter;
    if(!printAfter) {
        input.value = '';
    }
}

async function submitUpload() {
    const printerId = document.getElementById('uploadModal').dataset.printerId;
    const fileInput = document.getElementById('gcodeFile');
    const printAfter = document.getElementById('printAfterUpload').checked;
    const operatorInput = document.getElementById('uploadOperatorInitials');
    const operatorInitials = operatorInput.value.trim();

    if(!fileInput.files.length) {
        showToast('Please select a GCode file', 'error');
        return;
    }
    if(printAfter && !operatorInitials) {
        showToast('Operator initials are required to start a print', 'error');
        operatorInput.focus();
        return;
    }

    const file = fileInput.files[0];
    const formData = new FormData();
    formData.append('file', file);
    if(printAfter) {
        formData.append('operator_initials', operatorInitials);
    }

    const btn = document.getElementById('uploadBtn');
    btn.textContent = 'Uploading...';
    btn.disabled = true;

    try {
        const url = `/api/printers/${printerId}/upload${printAfter ? '?print_after=1' : ''}`;
        const resp = await fetch(url, { method: 'POST', body: formData });
        const result = await resp.json();

        if(result.success) {
            showToast(`Uploaded ${file.name} successfully`);
            hideModal('uploadModal');
        } else {
            showToast(formatUploadError(result, resp.status), 'error');
        }
    } catch (e) {
        showToast(`Upload error: ${e.message}`, 'error');
    } finally {
        btn.textContent = 'Upload';
        btn.disabled = false;
    }
}

function formatUploadError(result, statusCode) {
    if(result && result.error_type === 'upload_timeout') {
        return result.error || 'Upload timed out while sending the file to the printer';
    }
    if(statusCode === 400) {
        return `Invalid upload: ${result.error || 'Check the selected file and try again.'}`;
    }
    if(result && result.error_type === 'printer_api_error') {
        return result.error || 'Printer upload failed';
    }
    return `Upload failed: ${result && result.error ? result.error : 'Unknown error'}`;
}

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

async function showFilesModal(printerId, printerName) {
    document.getElementById('filesModalTitle').textContent = printerName;
    document.getElementById('filesList').innerHTML = '<div class="events-empty">Loading files...</div>';
    showModal('filesModal');

    try {
        const resp = await fetch(`/api/printers/${printerId}/files`);
        const data = await resp.json();

        if(data.error) {
            document.getElementById('filesList').innerHTML =
                `<div class="events-empty">${escapeHtml(data.error)}</div>`;
            return;
        }

        const files = extractFilesFromStorageResponse(data);

        if(files.length === 0) {
            const storageNames = extractStorageNames(data);
            const storageText = storageNames.length
                ? `Mounted storage: ${storageNames.map(escapeHtml).join(", ")}`
                : "No mounted storage detected from printer API";
            document.getElementById('filesList').innerHTML =
                `<div class="events-empty">No files found.<br><br>${storageText}</div>`;
            return;
        }

        document.getElementById('filesList').innerHTML = files.map(f => {
            const name = escapeHtml(f.display_name || f.name || f.filename || f.path || 'Unknown');
            const sizeKB = f.size ? (f.size / 1024).toFixed(0) + ' KB' : '';
            return `<div class="file-item">
                <span class="file-name">${name}</span>
                <span class="file-size">${sizeKB}</span>
            </div>`;
        }).join('');
    } catch (e) {
        document.getElementById('filesList').innerHTML =
            `<div class="events-empty">Error: ${escapeHtml(e.message)}</div>`;
    }
}

function extractStorageNames(data) {
    if(!data || typeof data !== 'object') {
        return [];
    }

    if(Array.isArray(data.storage_list)) {
        return data.storage_list
            .map(s => s.storage || s.name || s.path || '')
            .filter(Boolean);
    }

    return [];
}

function extractFilesFromStorageResponse(data) {
    const files = [];
    const walk = (node, prefix = '') => {
        if(!node || typeof node !== 'object') {
            return;
        }

        const type = String(node.type || '').toUpperCase();
        const name = String(node.display_name || node.name || node.filename || '');
        const path = String(node.path || '').trim();
        const fullPath = path || [prefix, name].filter(Boolean).join('/');

        if(type === 'FILE' || type === 'PRINT_FILE') {
            files.push({
                name,
                display_name: node.display_name || node.name || node.filename || fullPath,
                filename: node.filename || node.name || node.display_name || '',
                path: fullPath,
                size: Number(node.size) || 0
            });
        }

        const childKeys = ['children', 'files', 'items'];
        for(const key of childKeys) {
            if(Array.isArray(node[key])) {
                for (const child of node[key]) {
                    walk(child, fullPath || prefix);
                }
            }
        }
    };

    if(Array.isArray(data)) {
        data.forEach(item => walk(item));
        return files;
    }

    if(data && typeof data === 'object') {
        if(Array.isArray(data.storage_list)) {
            for(const storage of data.storage_list) {
                walk(storage);
            }
        } else {
            walk(data);
        }
    }

    // Deduplicate by path/name to avoid repeats when multiple trees include refs.
    const seen = new Set();
    return files.filter(f => {
        const key = `${f.path}|${f.name}`;

        if(seen.has(key)) {
            return false;
        }

        seen.add(key);

        return true;
    });
}

// ============================================================
// Spool Assignment (multi-tool aware)
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

// ============================================================
// Main Poll Loop
// ============================================================
async function poll() {
    try {
        const printersResp = await fetch("/api/printers");
        const printers = await printersResp.json();

        printerList = printers;

        // Check for status transitions → browser notifications
        checkPrinterTransitions(printers);

        document.getElementById("printerGrid").innerHTML = printers.map(renderCard).join("");
        updateStats(printers);

        const badge = document.getElementById("serverBadge");
        badge.textContent = "LIVE";
        badge.className = "status-badge";
        document.getElementById("sidebarDot").className = "status-dot";
        document.getElementById("sidebarStatus").textContent = "CONNECTED";

        const eventsResp = await fetch("/api/events/peek");
        const newEvents = await eventsResp.json();

        if(newEvents.length > 0) {
            for(const evt of newEvents) {
                const isDuplicate = events.some(e =>
                    e.timestamp === evt.timestamp &&
                    e.printer_id === evt.printer_id &&
                    e.type === evt.type
                );
                if(!isDuplicate) events.unshift(evt);
            }
            events = events.slice(0, 50);
        }

        const eventCount = document.getElementById("eventCount");
        const eventsList = document.getElementById("eventsList");
        eventCount.textContent = events.length;

        if(events.length > 0) {
            eventsList.innerHTML = events.map(renderEvent).join("");
        } else {
            eventsList.innerHTML = '<div class="events-empty">No events yet</div>';
        }

    } catch (err) {
        console.error("Poll failed:", err);
        document.getElementById("serverBadge").textContent = "OFFLINE";
        document.getElementById("serverBadge").className = "status-badge error";
        document.getElementById("sidebarDot").className = "status-dot offline";
        document.getElementById("sidebarStatus").textContent = "DISCONNECTED";
    }
}

// Start polling
poll();
setInterval(poll, POLL_INTERVAL);
