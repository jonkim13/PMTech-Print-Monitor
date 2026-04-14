// ============================================================
// Weekly Log - Event timeline renderer (grouped by day)
// ============================================================

var _WL_CATEGORY_META = {
    production: { label: 'Production', className: 'wl-cat-production' },
    error: { label: 'Error', className: 'wl-cat-error' },
    maintenance: { label: 'Maintenance', className: 'wl-cat-maintenance' },
    work_order: { label: 'Work Order', className: 'wl-cat-work-order' },
    assignment: { label: 'Assignment', className: 'wl-cat-assignment' },
    inventory: { label: 'Inventory', className: 'wl-cat-inventory' }
};

function wlCategoryMeta(category) {
    return _WL_CATEGORY_META[category]
        || { label: category || 'Event', className: 'wl-cat-default' };
}

function wlTimelineDayKey(iso) {
    if (!iso) return '';
    var d = new Date(iso);
    if (Number.isNaN(d.getTime())) return '';
    var y = d.getFullYear();
    var m = String(d.getMonth() + 1).padStart(2, '0');
    var day = String(d.getDate()).padStart(2, '0');
    return y + '-' + m + '-' + day;
}

function wlTimelineDayLabel(iso) {
    if (!iso) return 'Unknown Date';
    var d = new Date(iso);
    if (Number.isNaN(d.getTime())) return 'Unknown Date';
    return d.toLocaleDateString([], {
        weekday: 'long', month: 'long', day: 'numeric'
    });
}

function wlTimelineTimeLabel(iso) {
    if (!iso) return '—';
    var d = new Date(iso);
    if (Number.isNaN(d.getTime())) return '—';
    return d.toLocaleTimeString([], {
        hour: '2-digit', minute: '2-digit'
    });
}

function wlRenderTimeline(data) {
    var body = document.getElementById('wlTimelineBody');
    var count = document.getElementById('wlTimelineCount');
    if (!body) return;

    var events = (data && data.events) || [];
    if (count) count.textContent = events.length;

    if (events.length === 0) {
        body.innerHTML = '<div class="events-empty">'
            + 'No timeline events recorded for this week</div>';
        return;
    }

    // Group by local calendar day using the event's timestamp.
    var groups = [];
    var currentKey = null;
    events.forEach(function(e) {
        var key = wlTimelineDayKey(e.timestamp);
        if (key !== currentKey) {
            groups.push({
                key: key,
                label: wlTimelineDayLabel(e.timestamp),
                events: []
            });
            currentKey = key;
        }
        groups[groups.length - 1].events.push(e);
    });

    var html = groups.map(function(group) {
        var items = group.events.map(function(e) {
            var meta = wlCategoryMeta(e.category);
            var contextParts = [];
            if (e.printer) contextParts.push(escapeHtml(e.printer));
            if (e.operator) contextParts.push(escapeHtml(e.operator));
            if (e.customer) contextParts.push(escapeHtml(e.customer));
            var context = contextParts.length
                ? '<span class="wl-event-context">'
                    + contextParts.join(' • ') + '</span>'
                : '';
            return '<div class="wl-event-row">'
                + '<div class="wl-event-time">'
                + escapeHtml(wlTimelineTimeLabel(e.timestamp)) + '</div>'
                + '<div class="wl-event-dot ' + meta.className + '"></div>'
                + '<div class="wl-event-body">'
                + '<span class="wl-event-cat ' + meta.className + '">'
                + escapeHtml(meta.label) + '</span> '
                + '<span class="wl-event-desc">'
                + escapeHtml(e.description || '') + '</span> '
                + context
                + '</div>'
                + '</div>';
        }).join('');
        return '<div class="wl-timeline-day">'
            + '<div class="wl-day-header">' + escapeHtml(group.label) + '</div>'
            + '<div class="wl-day-events">' + items + '</div>'
            + '</div>';
    }).join('');

    if (data.truncated) {
        html += '<div class="events-empty">'
            + 'Timeline truncated to ' + (data.cap || 500)
            + ' events — export CSV to see everything.</div>';
    }
    body.innerHTML = html;
}
