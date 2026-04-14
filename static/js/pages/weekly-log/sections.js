// ============================================================
// Weekly Log - Production / Materials / Equipment / Work Orders
// renderers. Each consumes one API payload and updates the DOM.
// ============================================================

function wlStatusBadge(status) {
    // Reuse the core queue-status formatter so completed/failed look
    // identical to the Work Orders page. The ``.queue-status`` wrapper
    // comes from workorders.css and pairs with ``qs-<state>`` modifiers.
    var formatted = formatQueueStatus(status || 'unknown');
    return '<span class="queue-status ' + formatted.cssClass + '">'
        + escapeHtml(formatted.label) + '</span>';
}

function wlOutcomeBadge(outcome) {
    var o = String(outcome || 'unknown').toLowerCase();
    var cls = 'outcome-unknown';
    var label = '—';
    if (o === 'pass') { cls = 'outcome-pass'; label = 'Pass'; }
    else if (o === 'fail') { cls = 'outcome-fail'; label = 'Fail'; }
    else if (o === 'unknown') { cls = 'outcome-unknown'; label = '—'; }
    else { label = o; }
    return '<span class="outcome-badge ' + cls + '">'
        + escapeHtml(label) + '</span>';
}

function wlFormatDateOnly(iso) {
    if (!iso) return '—';
    var d = new Date(iso);
    if (Number.isNaN(d.getTime())) return '—';
    return d.toLocaleDateString([], {
        month: 'short', day: 'numeric'
    }) + ' ' + d.toLocaleTimeString([], {
        hour: '2-digit', minute: '2-digit'
    });
}

function wlFormatHours(hours) {
    var n = Number(hours);
    if (!Number.isFinite(n) || n <= 0) return '—';
    if (n < 1) return Math.round(n * 60) + 'm';
    return n.toFixed(1) + 'h';
}

// ---------------------------------------------------------------
// Production
// ---------------------------------------------------------------

function wlRenderProduction(data) {
    var body = document.getElementById('wlProductionBody');
    var count = document.getElementById('wlProductionCount');
    if (!body) return;

    var jobs = (data && data.jobs) || [];
    if (count) count.textContent = jobs.length;

    if (jobs.length === 0) {
        body.innerHTML = '<tr><td colspan="10" class="table-empty">'
            + 'No production activity recorded for this week</td></tr>';
        return;
    }

    body.innerHTML = jobs.map(function(job) {
        return '<tr>'
            + '<td>' + escapeHtml(wlFormatDateOnly(job.started_at)) + '</td>'
            + '<td>' + escapeHtml(job.printer_name || job.printer_id || '—') + '</td>'
            + '<td class="wl-file-cell" title="' + escapeHtml(job.file_name || '')
            + '">' + escapeHtml(job.file_name || '—') + '</td>'
            + '<td>' + wlStatusBadge(job.status) + '</td>'
            + '<td>' + escapeHtml(wlFormatHours(job.print_duration_hours)) + '</td>'
            + '<td>' + escapeHtml(job.operator_initials || '—') + '</td>'
            + '<td>' + escapeHtml(job.material || '—') + '</td>'
            + '<td>' + escapeHtml(job.spool_id || '—') + '</td>'
            + '<td>' + escapeHtml(String(job.filament_used_g || 0)) + '</td>'
            + '<td>' + wlOutcomeBadge(job.outcome) + '</td>'
            + '</tr>';
    }).join('');
}

// ---------------------------------------------------------------
// Materials
// ---------------------------------------------------------------

function wlRenderMaterials(data) {
    var body = document.getElementById('wlMaterialsUsageBody');
    var changes = document.getElementById('wlInventoryChanges');
    if (!body) return;

    var usage = (data && data.usage) || [];
    if (usage.length === 0) {
        body.innerHTML = '<tr><td colspan="6" class="table-empty">'
            + 'No material usage recorded for this week</td></tr>';
    } else {
        body.innerHTML = usage.map(function(row) {
            return '<tr>'
                + '<td>' + escapeHtml(row.spool_id || '—') + '</td>'
                + '<td>' + escapeHtml(row.material || '—') + '</td>'
                + '<td>' + escapeHtml(row.brand || '—') + '</td>'
                + '<td>' + escapeHtml(String(row.total_grams_used || 0)) + 'g</td>'
                + '<td>' + escapeHtml(String(row.prints_count || 0)) + '</td>'
                + '<td>' + escapeHtml((row.printers_used || []).join(', ') || '—') + '</td>'
                + '</tr>';
        }).join('');
    }

    if (!changes) return;

    var inv = (data && data.inventory_changes) || {};
    var added = inv.spools_added || [];
    var assigned = inv.assignments_changed || [];

    if (!added.length && !assigned.length) {
        changes.innerHTML = '<div class="events-empty">'
            + 'No inventory changes this week</div>';
        return;
    }

    var html = '';
    if (added.length) {
        html += '<div class="wl-inv-subhead">Spools Added</div>';
        html += '<div class="table-container"><table class="data-table">'
            + '<thead><tr>'
            + '<th>Spool ID</th><th>Material</th><th>Brand</th>'
            + '<th>Date</th><th>Grams</th></tr></thead><tbody>';
        added.forEach(function(s) {
            html += '<tr>'
                + '<td>' + escapeHtml(s.spool_id || '—') + '</td>'
                + '<td>' + escapeHtml(s.material || '—') + '</td>'
                + '<td>' + escapeHtml(s.brand || '—') + '</td>'
                + '<td>' + escapeHtml(s.date_added || '—') + '</td>'
                + '<td>' + escapeHtml(String(s.grams || 0)) + '</td>'
                + '</tr>';
        });
        html += '</tbody></table></div>';
    }
    if (assigned.length) {
        html += '<div class="wl-inv-subhead" style="margin-top: 16px;">'
            + 'Assignment Changes</div>';
        html += '<div class="table-container"><table class="data-table">'
            + '<thead><tr>'
            + '<th>Spool ID</th><th>Printer</th><th>Tool</th>'
            + '<th>Action</th><th>Date</th></tr></thead><tbody>';
        assigned.forEach(function(a) {
            html += '<tr>'
                + '<td>' + escapeHtml(a.spool_id || '—') + '</td>'
                + '<td>' + escapeHtml(a.printer || '—') + '</td>'
                + '<td>' + escapeHtml(String(a.tool || 0)) + '</td>'
                + '<td>' + escapeHtml(a.action || '—') + '</td>'
                + '<td>' + escapeHtml(wlFormatDateOnly(a.date)) + '</td>'
                + '</tr>';
        });
        html += '</tbody></table></div>';
    }
    changes.innerHTML = html;
}

// ---------------------------------------------------------------
// Equipment
// ---------------------------------------------------------------

function wlRenderEquipment(data) {
    var container = document.getElementById('wlEquipmentCards');
    if (!container) return;

    var printers = (data && data.printers) || [];
    if (printers.length === 0) {
        container.innerHTML = '<div class="events-empty">'
            + 'No printer data for this week</div>';
        return;
    }

    container.innerHTML = printers.map(function(p) {
        var util = Math.max(0, Math.min(100, Number(p.utilization_pct) || 0));
        var errorList = (p.errors || []).map(function(e) {
            return '<li><span class="wl-event-time">'
                + escapeHtml(wlFormatDateOnly(e.timestamp))
                + '</span> ' + escapeHtml(e.filename || e.event_type || 'Error')
                + '</li>';
        }).join('');
        var maintList = (p.maintenance || []).map(function(m) {
            var label = m.event_type || 'maintenance';
            if (m.details) label += ': ' + m.details;
            return '<li><span class="wl-event-time">'
                + escapeHtml(wlFormatDateOnly(m.timestamp))
                + '</span> ' + escapeHtml(label) + '</li>';
        }).join('');

        return '<div class="wl-equipment-card">'
            + '<div class="wl-eq-head">'
            + '<div class="wl-eq-name">' + escapeHtml(p.printer_name || p.printer_id) + '</div>'
            + '<div class="wl-eq-hours">' + (p.print_hours || 0) + 'h</div>'
            + '</div>'
            + '<div class="wl-eq-stats">'
            + '<div><span class="stat-label">Completed</span>'
            + '<span class="stat-value green">' + (p.prints_completed || 0) + '</span></div>'
            + '<div><span class="stat-label">Failed</span>'
            + '<span class="stat-value red">' + (p.prints_failed || 0) + '</span></div>'
            + '<div><span class="stat-label">Errors</span>'
            + '<span class="stat-value orange">' + (p.errors || []).length + '</span></div>'
            + '<div><span class="stat-label">Maintenance</span>'
            + '<span class="stat-value purple">' + (p.maintenance || []).length + '</span></div>'
            + '</div>'
            + '<div class="wl-util-row">'
            + '<div class="wl-util-label">Utilization <b>'
            + util.toFixed(1) + '%</b></div>'
            + '<div class="wl-util-bar"><div class="wl-util-fill" '
            + 'style="width: ' + util + '%"></div></div>'
            + '</div>'
            + (errorList ? '<div class="wl-eq-sublist"><b>Errors</b><ul>'
                + errorList + '</ul></div>' : '')
            + (maintList ? '<div class="wl-eq-sublist"><b>Maintenance</b><ul>'
                + maintList + '</ul></div>' : '')
            + '</div>';
    }).join('');
}

// ---------------------------------------------------------------
// Work Orders
// ---------------------------------------------------------------

function wlRenderWorkOrders(data) {
    var partsEl = document.getElementById('wlPartsSummary');
    var parts = (data && data.parts_summary) || {};
    if (partsEl) {
        partsEl.innerHTML =
            wlPartsStatCard('Parts Completed',
                parts.completed_this_week || 0, 'green')
            + wlPartsStatCard('Parts Started',
                parts.started_this_week || 0, 'blue')
            + wlPartsStatCard('Parts Failed',
                parts.failed_this_week || 0, 'red')
            + wlPartsStatCard('Parts Cancelled',
                parts.cancelled_this_week || 0, 'orange');
    }

    wlRenderWoGroup('wlOrdersCreated',
        (data && data.orders_created) || [],
        'No work orders created this week');
    wlRenderWoGroup('wlOrdersCompleted',
        (data && data.orders_completed) || [],
        'No work orders completed this week');
    wlRenderWoGroup('wlOrdersActive',
        (data && data.orders_active) || [],
        'No active work orders during this week');
}

function wlPartsStatCard(label, value, color) {
    return '<div class="wl-stat-card">'
        + '<span class="stat-label">' + escapeHtml(label) + '</span>'
        + '<span class="stat-value ' + color + '">'
        + escapeHtml(String(value || 0)) + '</span>'
        + '</div>';
}

function wlRenderWoGroup(containerId, orders, emptyMessage) {
    var el = document.getElementById(containerId);
    if (!el) return;
    if (!orders.length) {
        el.innerHTML = '<div class="events-empty">'
            + escapeHtml(emptyMessage) + '</div>';
        return;
    }
    var rows = orders.map(function(wo) {
        var total = wo.total_parts || 0;
        var done = wo.parts_completed || 0;
        var failed = wo.parts_failed || 0;
        var progress = total > 0
            ? (done + '/' + total + (failed ? ' (' + failed + ' failed)' : ''))
            : '—';
        var wos = formatWoStatus(wo.status || 'unknown');
        var dateLabel = wo.completed_at
            ? wlFormatDateOnly(wo.completed_at)
            : wlFormatDateOnly(wo.created_at);
        return '<tr>'
            + '<td>' + escapeHtml(wo.wo_id || '—') + '</td>'
            + '<td>' + escapeHtml(wo.customer_name || '—') + '</td>'
            + '<td>' + escapeHtml(dateLabel) + '</td>'
            + '<td>' + escapeHtml(progress) + '</td>'
            + '<td><span class="wo-status ' + wos.cssClass + '">'
            + escapeHtml(wos.label) + '</span></td>'
            + '</tr>';
    }).join('');
    el.innerHTML = '<div class="table-container"><table class="data-table">'
        + '<thead><tr>'
        + '<th>WO ID</th><th>Customer</th><th>Date</th>'
        + '<th>Parts</th><th>Status</th></tr></thead>'
        + '<tbody>' + rows + '</tbody></table></div>';
}
