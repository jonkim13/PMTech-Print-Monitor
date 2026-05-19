// ============================================================
// Work Orders - Triage tab (Phase 2.5b)
// 5-lane decision board + active-parts table.
// 2500ms poll while the Triage subtab is active.
// All interpolated text goes through escapeHtml.
// ============================================================

const TRIAGE_POLL_MS = 2500;
let _triagePollTimer = null;
let _triagePollInflight = false;
// Cache of active-parts rows keyed by queue_id — lets the Print modal
// fall back to this data when _woDetailQueueItems is empty.
var _triageActivePartsByQueueId = {};
// Active-parts cache keyed by wo_id, for drilling into a row's WO.
var _triageActivePartsByWo = {};

const TRIAGE_SNOOZE_PREFIX = 'triage.snooze.spool.';
const TRIAGE_DISMISS_PREFIX = 'triage.dismiss.queue.';

function startTriagePoll() {
    if (_triagePollTimer) return;
    loadTriage();
    _triagePollTimer = setInterval(loadTriage, TRIAGE_POLL_MS);
}

function stopTriagePoll() {
    if (_triagePollTimer) {
        clearInterval(_triagePollTimer);
        _triagePollTimer = null;
    }
}

async function loadTriage() {
    if (_triagePollInflight) return;
    _triagePollInflight = true;
    try {
        var payload = await apiGet('/api/triage');
        var lanes = filterTriageLanes(payload.lanes || []);
        renderTriageHeader(lanes);
        lanes.forEach(renderLane);
        renderActiveParts(payload.active_parts || []);
        // Sidebar Work Orders badge reflects the visible-after-filter total.
        var visibleTotal = lanes.reduce(function (sum, l) {
            return sum + (l.count || 0);
        }, 0);
        if (typeof updateSidebarAttnBadge === 'function') {
            updateSidebarAttnBadge(visibleTotal);
        }
    } catch (e) {
        // Silently drop; retry next tick.
        console.error('Triage poll failed:', e);
    } finally {
        _triagePollInflight = false;
    }
}

// ------------------------------------------------------------
// Client-side filters: snoozed spools + dismissed cancelled items
// (both will be persisted server-side in a future phase; for now,
// localStorage keeps them out of the operator's way between polls)
// ------------------------------------------------------------

function filterTriageLanes(lanes) {
    var now = Date.now();
    return lanes.map(function (lane) {
        if (lane.kind === 'external_spool') {
            var kept = (lane.items || []).filter(function (it) {
                if (it.kind !== 'spool-low') return true;
                var key = TRIAGE_SNOOZE_PREFIX + it.printer_id + '.' + it.tool_index;
                var until = parseInt(localStorage.getItem(key) || '0', 10);
                return !(until && until > now);
            });
            return Object.assign({}, lane, { items: kept, count: kept.length });
        }
        if (lane.kind === 'failed') {
            var kept = (lane.items || []).filter(function (it) {
                if (it.kind !== 'cancelled') return true;
                var key = TRIAGE_DISMISS_PREFIX + it.queue_id;
                return !localStorage.getItem(key);
            });
            return Object.assign({}, lane, { items: kept, count: kept.length });
        }
        return lane;
    });
}

// ------------------------------------------------------------
// Header
// ------------------------------------------------------------

function renderTriageHeader(lanes) {
    var total = lanes.reduce(function (sum, l) { return sum + (l.count || 0); }, 0);
    var subtitle = document.getElementById('triageSubtitle');
    if (subtitle) {
        subtitle.textContent = total === 0
            ? 'No decisions waiting'
            : total + ' decision' + (total === 1 ? '' : 's') + ' waiting';
    }
}

// ------------------------------------------------------------
// Lane rendering
// ------------------------------------------------------------

function renderLane(lane) {
    var countEl = document.getElementById('lane-' + lane.kind + '-count');
    var bodyEl = document.getElementById('lane-' + lane.kind + '-body');
    if (countEl) countEl.textContent = lane.count || 0;
    if (!bodyEl) return;

    // Preserve operator scroll position across the 2.5s poll re-render.
    var prevScrollTop = bodyEl.scrollTop;

    if (!lane.items || lane.items.length === 0) {
        var msg = bodyEl.dataset.laneEmptyMessage || 'Nothing here yet';
        bodyEl.innerHTML = '<div class="lane-empty">' + escapeHtml(msg) + '</div>';
        return;
    }

    bodyEl.innerHTML = lane.items.map(function (item) {
        return renderLaneCard(item, lane.kind);
    }).join('');
    bodyEl.scrollTop = prevScrollTop;
}

function renderLaneCard(item, laneKind) {
    var cardTone = laneToneClass(laneKind, item.kind);
    var actions = renderLaneActions(laneKind, item);
    var meta = renderLaneMeta(laneKind, item);
    var titleClick = renderLaneTitleClickAttr(laneKind, item);

    var titleHtml = '<div class="lane-card-title"' + titleClick + '>' +
        escapeHtml(item.title || '') + '</div>';
    var metaHtml = meta
        ? '<div class="lane-card-meta">' + meta + '</div>'
        : '';
    var subHtml = item.sub
        ? '<div class="lane-card-sub">' + escapeHtml(item.sub) + '</div>'
        : '';

    return '<div class="attn-card ' + cardTone + ' lane-card" data-item-kind="' +
        escapeHtml(item.kind || '') + '">' +
        titleHtml + metaHtml + subHtml + actions +
        '</div>';
}

function laneToneClass(laneKind, itemKind) {
    if (laneKind === 'failed') return 'failed';
    if (laneKind === 'qc') return 'qc';
    if (laneKind === 'external_spool') {
        return itemKind === 'spool-low' ? 'spool' : 'vendor';
    }
    if (laneKind === 'ready_ship') return 'qc';  // info tone
    if (laneKind === 'design_await') return 'busy';
    return '';
}

function renderLaneTitleClickAttr(laneKind, item) {
    if (item.wo_id) {
        return ' onclick="openWOFromTriage(\'' +
            escapeHtml(item.wo_id) + '\')" style="cursor:pointer;"';
    }
    if (item.printer_id) {
        // Spool-low cards: clicking opens the Assign Spool modal.
        return ' onclick="showAssignSpoolModal(\'' +
            escapeHtml(item.printer_id) + '\', \'' +
            escapeHtml(item.printer_name || item.printer_id) +
            '\')" style="cursor:pointer;"';
    }
    return '';
}

function renderLaneMeta(laneKind, item) {
    var parts = [];
    if (item.wo_id) {
        parts.push('<span class="tag info">' + escapeHtml(item.wo_id) + '</span>');
    }
    if (item.customer) {
        parts.push('<span class="muted lane-card-customer">' + escapeHtml(item.customer) + '</span>');
    }
    if (laneKind === 'external_spool' && item.kind === 'spool-low') {
        var pct = Math.round((item.percent || 0) * 100);
        parts.push('<span class="mono spool-pct">' + pct + '% · ' +
            escapeHtml(String(item.grams_left || 0)) + 'g</span>');
    }
    if (laneKind === 'failed' && item.failed_at) {
        parts.push('<span class="muted mono lane-card-ts">' +
            escapeHtml(item.failed_at) + '</span>');
    }
    return parts.length ? parts.join(' ') : '';
}

function renderLaneActions(laneKind, item) {
    var primary = '';
    var secondary = '';

    if (laneKind === 'failed') {
        if (item.kind === 'auto-fail') {
            primary = laneBtn('Redo print', 'primary',
                "openPrintModalFromTriage(" + item.queue_id + ")");
            secondary = laneBtn('Cancel part', 'danger',
                "cancelQueueItemFromTriage(" + item.queue_id + ", '" +
                escapeHtml(item.part_name || '') + "')");
        } else if (item.kind === 'cancelled') {
            primary = laneBtn('Print', 'primary',
                "openPrintModalFromTriage(" + item.queue_id + ")");
            secondary = laneBtn('Remove', 'ghost',
                "dismissCancelledFromTriage(" + item.queue_id + ")");
        }
    } else if (laneKind === 'qc' && item.kind === 'internal-qc') {
        primary = laneBtn('Inspect', 'primary',
            "openInspectionFromTriage(" + item.job_id + ")");
        // 'Assign inspector' deliberately hidden in 2.5b (Phase B).
    } else if (laneKind === 'external_spool' && item.kind === 'spool-low') {
        primary = laneBtn('Swap spool', 'primary',
            "showAssignSpoolModal('" + escapeHtml(item.printer_id) +
            "', '" + escapeHtml(item.printer_name || item.printer_id) + "')");
        secondary = laneBtn('Snooze 1h', 'ghost',
            "snoozeSpoolFromTriage('" + escapeHtml(item.printer_id) +
            "', " + (item.tool_index || 0) + ")");
    }

    if (!primary && !secondary) return '';
    return '<div class="lane-card-actions">' + primary + secondary + '</div>';
}

function laneBtn(label, variant, onclick) {
    return '<button class="btn sm ' + variant + '" onclick="' + onclick +
        '">' + escapeHtml(label) + '</button>';
}

// ------------------------------------------------------------
// Active parts table
// ------------------------------------------------------------

function renderActiveParts(parts) {
    var body = document.getElementById('activePartsBody');
    if (!body) return;

    _triageActivePartsByQueueId = {};
    _triageActivePartsByWo = {};
    parts.forEach(function (p) {
        if (p.queue_id) _triageActivePartsByQueueId[p.queue_id] = p;
        if (p.wo_id) {
            if (!_triageActivePartsByWo[p.wo_id]) _triageActivePartsByWo[p.wo_id] = [];
            _triageActivePartsByWo[p.wo_id].push(p);
        }
    });

    var subtitle = document.getElementById('activePartsSubtitle');
    if (subtitle) {
        var printingCount = parts.filter(function (p) { return p.status === 'printing'; }).length;
        var queuedCount = parts.filter(function (p) { return p.status === 'queued'; }).length;
        subtitle.textContent = parts.length === 0
            ? ''
            : queuedCount + ' queued · ' + printingCount + ' printing · across ' +
              new Set(parts.map(function (p) { return p.wo_id; })).size + ' WO' +
              (new Set(parts.map(function (p) { return p.wo_id; })).size === 1 ? '' : 's');
    }

    if (parts.length === 0) {
        body.innerHTML = '<div class="active-parts-empty">No active parts</div>';
        return;
    }
    // Preserve scroll position across poll re-renders.
    var prevScrollTop = body.scrollTop;
    body.innerHTML = parts.map(renderActivePartsRow).join('');
    body.scrollTop = prevScrollTop;
}

function renderActivePartsRow(p) {
    var woTagTone = p.status === 'failed' ? 'err'
        : p.status === 'external' ? 'busy' : 'info';
    var statusInfo = typeof formatQueueStatus === 'function'
        ? formatQueueStatus(p.status)
        : { label: String(p.status || '').toUpperCase(), cssClass: '' };

    return '<div class="active-parts-grid active-parts-row" ' +
        'onclick="openWOFromTriage(\'' + escapeHtml(p.wo_id) + '\')" ' +
        'style="cursor:pointer;">' +
        '<span class="mono tab muted">' + escapeHtml(p.seq || '') + '</span>' +
        '<span class="tag ' + woTagTone + '">' + escapeHtml(p.wo_id || '') + '</span>' +
        '<span class="active-parts-part">' + escapeHtml(p.part_name || '') + '</span>' +
        '<span class="job-type ' + escapeHtml(p.job_type || 'internal') + '">' +
        escapeHtml((p.job_type || 'internal').toUpperCase()) + '</span>' +
        '<span class="muted">' + escapeHtml(p.material || '') + '</span>' +
        '<span class="active-parts-status ' + escapeHtml(statusInfo.cssClass || '') + '">' +
        escapeHtml(statusInfo.label || '') + '</span>' +
        '<span class="mono muted">' + escapeHtml(p.printer || '') + '</span>' +
        '<span class="mono tab">' + escapeHtml(p.eta || '') + '</span>' +
        '</div>';
}

// ------------------------------------------------------------
// Action dispatchers (called from inline onclick — must be global)
// ------------------------------------------------------------

function openWOFromTriage(woId, focusId) {
    if (!woId) return;
    // Phase 2.5c: WO Detail is now its own route. Use real navigation
    // so browser back / shareable links work natively.
    var url = '/work-orders/' + encodeURIComponent(woId) + '?from=triage';
    if (focusId) url += '&focus=' + encodeURIComponent(focusId);
    window.location.href = url;
}

async function openPrintModalFromTriage(queueId) {
    // Pre-stuff _woDetailQueueItems so showQueuePrintModal's fallback
    // can read the part metadata without needing /api/queue (deleted
    // in 2.5b).
    var cached = _triageActivePartsByQueueId[queueId];
    if (!cached) {
        // Active parts table doesn't include cancelled/failed items
        // (those are terminal). Build a minimal record from the lane
        // data; the Print modal copes with this minimal shape.
        cached = { queue_id: queueId };
    }
    if (typeof _woDetailQueueItems !== 'undefined') {
        _woDetailQueueItems = [{
            queue_id: cached.queue_id,
            part_name: cached.part_name || '',
            sequence_number: parseQueueSeq(cached.seq).seq,
            total_quantity: parseQueueSeq(cached.seq).total,
            material: cached.material || '',
            customer_name: cached.customer || '',
            wo_id: cached.wo_id || '',
            job_id: cached.job_id || null,
            status: cached.status || 'queued',
        }];
    }
    showQueuePrintModal(queueId);
}

function parseQueueSeq(seq) {
    if (!seq || typeof seq !== 'string') return { seq: 1, total: 1 };
    var parts = seq.split('/');
    return {
        seq: parseInt(parts[0], 10) || 1,
        total: parseInt(parts[1], 10) || 1,
    };
}

async function cancelQueueItemFromTriage(queueId, partName) {
    if (!confirm('Cancel ' + (partName || 'this part') + '?')) return;
    try {
        await apiPost('/api/queue/' + queueId + '/cancel', {});
        showToast('Cancelled');
        loadTriage();
    } catch (e) {
        showToast('Error: ' + e.message, 'error');
    }
}

function openInspectionFromTriage(jobId) {
    if (typeof showJobDetail === 'function') {
        showJobDetail(jobId);
    } else {
        showToast('Inspection modal not available', 'error');
    }
}

function dismissCancelledFromTriage(queueId) {
    // TODO: persist server-side. For now, localStorage so the row
    // doesn't reappear on the next poll for this browser session.
    localStorage.setItem(TRIAGE_DISMISS_PREFIX + queueId, String(Date.now()));
    showToast('Removed from Triage');
    loadTriage();
}

function snoozeSpoolFromTriage(printerId, toolIndex) {
    var key = TRIAGE_SNOOZE_PREFIX + printerId + '.' + toolIndex;
    var until = Date.now() + (60 * 60 * 1000);  // 1 hour
    // TODO: persist server-side.
    localStorage.setItem(key, String(until));
    showToast('Snoozed for 1 hour');
    loadTriage();
}
