// ============================================================
// Dashboard - Aggregated /api/dashboard poll loop (5s)
// Replaces innerHTML of four containers per tick.
// All interpolated text passes through escapeHtml.
// ============================================================

const DASHBOARD_POLL_MS = 5000;
let _dashPollTimer = null;
let _dashPollInflight = false;

function startDashboardPoll() {
    if (_dashPollTimer) return;
    pollDashboard();
    _dashPollTimer = setInterval(pollDashboard, DASHBOARD_POLL_MS);
}

function stopDashboardPoll() {
    if (_dashPollTimer) {
        clearInterval(_dashPollTimer);
        _dashPollTimer = null;
    }
}

async function pollDashboard() {
    if (_dashPollInflight) return;
    _dashPollInflight = true;
    try {
        const r = await apiGet('/api/dashboard');
        renderDashStats(r.stats || {});
        renderPrinterGrid(r.printers || []);
        renderAttentionRail(r.attention_items || [], r.attention_total || 0);
        renderEvents(r.events || []);
        if (typeof updateSidebarAttnBadge === 'function') {
            updateSidebarAttnBadge(r.attention_total || 0);
        }
        if (Array.isArray(r.printers)) {
            // Keep existing browser-notification logic working off the
            // same aggregated poll — no separate /api/printers fetch.
            printerList = r.printers;
            if (typeof checkPrinterTransitions === 'function') {
                checkPrinterTransitions(r.printers.map(p => ({
                    printer_id: p.id, name: p.name, status: p.status,
                    job: { filename: p.part || '' }
                })));
            }
        }
    } catch (e) {
        // Silently drop; retry next tick.
        console.error('Dashboard poll failed:', e);
    } finally {
        _dashPollInflight = false;
    }
}

// ------------------------------------------------------------
// Renderers — each replaces a single container's innerHTML.
// ------------------------------------------------------------

function renderDashStats(stats) {
    const setStat = (id, value, secondary, hint) => {
        const root = document.getElementById(id);
        if (!root) return;
        const v = root.querySelector('[data-stat-value]');
        const s = root.querySelector('[data-stat-secondary]');
        const h = root.querySelector('[data-stat-hint]');
        if (v) v.textContent = (value !== undefined && value !== null) ? String(value) : '0';
        if (s) {
            if (secondary !== undefined && secondary !== null && secondary !== '') {
                s.textContent = String(secondary);
                s.style.display = '';
            } else {
                s.style.display = 'none';
            }
        }
        if (h && hint !== undefined) h.textContent = hint;
    };

    const total = Number(stats.printers_total) || 0;
    const printing = Number(stats.printers_printing) || 0;
    setStat('statPrinters', printing, '/ ' + total, 'printing now');

    setStat('statDoneToday', Number(stats.done_today) || 0, null, 'parts completed');

    const qc = Number(stats.awaiting_qc) || 0;
    const qcWoCount = Number(stats.awaiting_qc_wo_count) || 0;
    setStat('statAwaitingQc', qc, null, 'across ' + qcWoCount + ' WO' + (qcWoCount === 1 ? '' : 's'));

    const late = Number(stats.late_wos) || 0;
    setStat('statLateWos', late, null, 'past due date');
}

function renderPrinterGrid(printers) {
    const grid = document.getElementById('printerGrid');
    if (!grid) return;
    if (!printers.length) {
        grid.innerHTML = '<div class="card card-pad muted" style="grid-column: 1 / -1; text-align:center; font-size:12px;">No printers configured</div>';
        return;
    }
    grid.innerHTML = printers.map(renderPrinterCard).join('');
    refreshIcons(grid);
}

function renderPrinterCard(p) {
    const status = String(p.status || 'idle');
    const isPrinting = status === 'printing';
    const isError = status === 'error';
    const isFinished = status === 'finished';
    const isOffline = status === 'offline';

    const accent = isPrinting ? 's-printing'
        : isError ? 's-error'
        : isFinished ? 's-finished'
        : isOffline ? 's-offline'
        : 's-idle';

    const idEnc = encodeURIComponent(p.id || '');
    const nameEnc = encodeURIComponent(p.name || '');
    const safeName = escapeHtml(p.name);

    let jobLine = '';
    if (p.part) {
        const safePart = escapeHtml(p.part);
        const safeSeq = p.part_seq ? '<span class="muted">&middot; ' + escapeHtml(p.part_seq) + '</span>' : '';
        const woTag = p.wo ? '<span style="margin-left:8px;"><span class="tag info">' + escapeHtml(p.wo) + '</span></span>' : '';
        jobLine = '<div class="pc-job">Printing <span class="pc-job-part">' + safePart + '</span> ' + safeSeq + woTag + '</div>';
    } else if (status === 'idle') {
        const lp = p.last_print_relative ? ' &middot; last print ' + escapeHtml(p.last_print_relative) + ' ago' : '';
        jobLine = '<div class="pc-job muted">Ready' + lp + '</div>';
    } else if (status === 'offline') {
        jobLine = '<div class="pc-job muted">Offline</div>';
    } else {
        jobLine = '<div class="pc-job muted">No active print</div>';
    }

    let body = '';
    if (isPrinting) {
        body = '<div class="pc-progress">' + renderProgressBar(p.progress || 0, p.eta_text, true) + '</div>';
    } else if (isError) {
        const errTitle = escapeHtml(p.error_title || 'Printer error');
        const errSub = p.error_sub ? '<div class="err-sub">' + escapeHtml(p.error_sub) + '</div>' : '';
        body = '<div class="pc-error-banner"><div class="err-box">' +
               '<i data-lucide="alert-triangle" class="icon icon-sm"></i>' +
               '<div><div class="err-title">' + errTitle + '</div>' + errSub + '</div></div></div>';
    }

    const spoolsHtml = (p.spools && p.spools.length)
        ? p.spools.map(renderSpoolPill).join('')
        : '<span class="muted" style="font-size:11px;">No spool assigned</span>';

    const noz = p.nozzle || { cur: 0, tgt: 0 };
    const bed = p.bed || { cur: 0, tgt: 0 };

    const stopBtn = isPrinting
        ? '<button class="btn danger" onclick="stopPrint(decodeURIComponent(\'' + idEnc + '\'), decodeURIComponent(\'' + nameEnc + '\'))"><i data-lucide="square" class="icon icon-sm"></i> Stop</button>'
        : '<button class="btn ghost" disabled><i data-lucide="square" class="icon icon-sm"></i> Stop</button>';

    return '<div class="card pc ' + accent + '" data-printer-id="' + escapeHtml(p.id) + '">' +
        '<div class="pc-accent"></div>' +
        '<div class="pc-head">' +
            '<div style="flex:1;min-width:0;">' +
                '<div style="display:flex;align-items:center;gap:10px;">' +
                    '<h3>' + safeName + '</h3>' +
                    renderStatusPill(status) +
                '</div>' +
                jobLine +
            '</div>' +
        '</div>' +
        body +
        '<div class="pc-spools-row">' +
            '<div class="pc-spools-list">' + spoolsHtml + '</div>' +
            '<div class="pc-temps">' +
                renderTemp('Noz', noz.cur, noz.tgt) +
                renderTemp('Bed', bed.cur, bed.tgt) +
            '</div>' +
        '</div>' +
        '<div class="pc-actions">' +
            '<button class="btn" onclick="showFilesModal(decodeURIComponent(\'' + idEnc + '\'), decodeURIComponent(\'' + nameEnc + '\'))">Files</button>' +
            '<button class="btn" onclick="showAssignSpoolModal(decodeURIComponent(\'' + idEnc + '\'), decodeURIComponent(\'' + nameEnc + '\'))">Assign spool</button>' +
            '<div class="pc-spacer"></div>' +
            stopBtn +
        '</div>' +
    '</div>';
}

function renderStatusPill(kind) {
    const labels = {
        printing: 'PRINTING', queued: 'QUEUED', idle: 'IDLE', done: 'DONE',
        failed: 'FAILED', cancel: 'CANCELLED', attn: 'ATTENTION', busy: 'BUSY',
        offline: 'OFFLINE', error: 'ERROR', finished: 'FINISHED',
        unknown: 'UNKNOWN'
    };
    const label = labels[kind] || String(kind).toUpperCase();
    return '<span class="st st-' + kind + '"><i class="sym sym-' + kind + '"></i><span>' + label + '</span></span>';
}

function renderProgressBar(percent, eta, large) {
    const p = Math.max(0, Math.min(100, Math.floor((Number(percent) || 0) * 100)));
    const etaHtml = eta ? '<span class="progress-eta">' + escapeHtml(eta) + '</span>' : '';
    return '<div class="bar' + (large ? ' lg' : '') + '"><i style="width:' + p + '%;"></i></div>' +
        '<div class="progress-meta"><span class="progress-pct">' + p + '%</span>' + etaHtml + '</div>';
}

function renderSpoolPill(s) {
    const hasMat = s.material && String(s.material).length > 0;
    const pct = Number(s.percent) || 0;
    const tone = pct <= 0.05 ? 'empty' : (pct < 0.25 ? 'low' : 'ok');
    const swatchStyle = s.color ? ' style="background:' + escapeHtml(s.color) + ';"' : '';
    const slot = escapeHtml(s.slot || '');
    const mat = hasMat ? escapeHtml(s.material) : 'empty';
    const colorName = s.color_name ? ' ' + escapeHtml(s.color_name) : '';
    const pctInt = Math.max(0, Math.min(100, Math.floor(pct * 100)));

    let inner = '<span class="swatch"' + swatchStyle + '></span>' +
        '<span style="font-weight:600;color:var(--fg-2);">' + slot + '</span>' +
        '<span class="label">' + mat + (hasMat ? colorName : '') + '</span>';
    if (hasMat) {
        inner += '<span style="display:inline-flex;align-items:center;gap:4px;margin-left:4px;">' +
            '<span class="gauge" style="width:28px;"><i class="' + tone + '" style="width:' + pctInt + '%;"></i></span>' +
            '<span class="mono tab" style="font-size:9px;color:var(--fg-3);">' + pctInt + '%</span>' +
        '</span>';
    }
    return '<div class="tool' + (hasMat ? '' : ' empty') + '">' + inner + '</div>';
}

function renderTemp(label, cur, tgt) {
    const tgtN = Math.round(Number(tgt) || 0);
    const curN = Math.round(Number(cur) || 0);
    const hot = tgtN > 0;
    return '<div class="temp-readout">' +
        '<span class="k">' + escapeHtml(label) + '</span>' +
        '<span class="temp-val mono tab">' +
            '<span class="temp-cur' + (hot ? ' hot' : '') + '">' + curN + '&deg;</span>' +
            '<span class="temp-tgt"> / ' + tgtN + '&deg;</span>' +
        '</span>' +
    '</div>';
}

function renderAttentionRail(items, total) {
    const rail = document.getElementById('attentionRail');
    const badge = document.getElementById('railAttnBadge');
    if (!rail) return;

    if (badge) {
        if (total > 0) {
            badge.textContent = total;
            badge.style.display = '';
        } else {
            badge.style.display = 'none';
        }
    }

    if (!items.length || total === 0) {
        rail.innerHTML = '<div class="rail-empty">No decisions waiting</div>';
        return;
    }

    rail.innerHTML = items.map(renderAttnCard).join('');
}

function renderAttnCard(item) {
    const kind = String(item.kind || 'warn');
    const toneColor = { failed: 'err', qc: 'info', spool: 'warn', busy: 'busy' }[kind] || 'warn';
    const label = escapeHtml(item.label || '');
    const title = escapeHtml(item.title || '');
    const sub = item.sub ? '<div class="muted" style="font-size:11.5px;margin-top:2px;">' + escapeHtml(item.sub) + '</div>' : '';
    const tsLabel = item.timestamp_label ? '<span class="muted mono" style="font-size:9px;margin-left:auto;">' + escapeHtml(item.timestamp_label) + '</span>' : '';

    let actions = '';
    if (Array.isArray(item.actions) && item.actions.length) {
        actions = '<div style="display:flex;gap:6px;margin-top:8px;">' +
            item.actions.map(a => {
                const variant = a.variant ? ' ' + escapeHtml(a.variant) : '';
                const onclick = a.onclick ? ' onclick="' + escapeHtml(a.onclick) + '"' : '';
                return '<button class="btn sm' + variant + '"' + onclick + '>' + escapeHtml(a.label || '') + '</button>';
            }).join('') +
        '</div>';
    }

    return '<div class="attn-card ' + kind + '" data-attn-kind="' + kind + '">' +
        '<div style="display:flex;align-items:center;gap:6px;">' +
            '<span class="k" style="color:var(--' + toneColor + ');">' + label + '</span>' +
            tsLabel +
        '</div>' +
        '<div style="font-size:13px;font-weight:600;margin-top:4px;">' + title + '</div>' +
        sub +
        actions +
    '</div>';
}

function renderEvents(events) {
    const list = document.getElementById('eventsList');
    if (!list) return;
    if (!events.length) {
        list.innerHTML = '<div class="activity-empty">No events yet</div>';
        return;
    }
    list.innerHTML = events.map(renderEventRow).join('');
}

function renderEventRow(e) {
    const ts = escapeHtml(e.ts || '');
    const color = escapeHtml(e.color || 'neutral');
    const what = escapeHtml(e.what || '');
    const who = e.who ? '<span class="muted">&middot; by ' + escapeHtml(e.who) + '</span>' : '';
    const where = e.where ? '<span class="where">' + escapeHtml(e.where) + '</span>' : '';
    return '<div class="event">' +
        '<span class="ts">' + ts + '</span>' +
        '<span class="pulse" style="background:var(--' + color + ');"></span>' +
        '<span><span class="what">' + what + '</span> ' + who + '</span>' +
        where +
    '</div>';
}
