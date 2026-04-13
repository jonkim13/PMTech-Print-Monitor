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
