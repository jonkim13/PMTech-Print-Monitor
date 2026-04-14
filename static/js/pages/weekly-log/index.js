// ============================================================
// Weekly Log - State, week navigation, tab switching, loader
// ============================================================

// Current week state — plain ISO YYYY-MM-DD Monday string.
var _wlCurrentWeekStart = null;
var _wlCurrentTab = 'production';
// Cache the last-loaded payload per section so tab switches are instant.
var _wlSectionCache = {};
var _wlLoadToken = 0;

function wlMondayOfToday() {
    var now = new Date();
    // Use UTC day so this matches the backend window.
    var utcDay = now.getUTCDay();
    // JavaScript: Sunday=0, Monday=1 ... Saturday=6.
    // We want Monday as week start: offset = (day - 1 + 7) % 7.
    var offset = (utcDay + 6) % 7;
    var monday = new Date(Date.UTC(
        now.getUTCFullYear(), now.getUTCMonth(),
        now.getUTCDate() - offset
    ));
    return wlIsoDate(monday);
}

function wlIsoDate(d) {
    // d is a Date; return YYYY-MM-DD using UTC components.
    var y = d.getUTCFullYear();
    var m = String(d.getUTCMonth() + 1).padStart(2, '0');
    var day = String(d.getUTCDate()).padStart(2, '0');
    return y + '-' + m + '-' + day;
}

function wlParseIsoDate(iso) {
    // Build a Date from YYYY-MM-DD in UTC.
    var parts = String(iso).split('-');
    return new Date(Date.UTC(
        parseInt(parts[0], 10),
        parseInt(parts[1], 10) - 1,
        parseInt(parts[2], 10)
    ));
}

function wlAddDays(iso, days) {
    var d = wlParseIsoDate(iso);
    d.setUTCDate(d.getUTCDate() + days);
    return wlIsoDate(d);
}

function wlFormatWeekLabel(startIso, endIso) {
    var start = wlParseIsoDate(startIso);
    var end = wlParseIsoDate(endIso);
    var months = ['Jan', 'Feb', 'Mar', 'Apr', 'May', 'Jun',
                  'Jul', 'Aug', 'Sep', 'Oct', 'Nov', 'Dec'];
    var sameMonth = start.getUTCMonth() === end.getUTCMonth()
        && start.getUTCFullYear() === end.getUTCFullYear();
    var startLabel = months[start.getUTCMonth()] + ' '
        + start.getUTCDate();
    var endLabel = sameMonth
        ? end.getUTCDate()
        : months[end.getUTCMonth()] + ' ' + end.getUTCDate();
    return startLabel + ' – ' + endLabel + ', ' + end.getUTCFullYear();
}

// ---- Entry point invoked from switchPage() ----
function loadWeeklyLog() {
    if (!_wlCurrentWeekStart) {
        _wlCurrentWeekStart = wlMondayOfToday();
    }
    wlApplyWeekHeader();
    wlLoadAllSections();
}

function wlApplyWeekHeader() {
    var startIso = _wlCurrentWeekStart;
    var endIso = wlAddDays(startIso, 6);
    var label = document.getElementById('wlWeekLabel');
    if (label) {
        label.textContent = wlFormatWeekLabel(startIso, endIso);
    }
    var nextBtn = document.getElementById('wlNextWeek');
    if (nextBtn) {
        var currentMonday = wlMondayOfToday();
        nextBtn.disabled = (startIso >= currentMonday);
    }
}

function wlNavigateWeek(direction) {
    var candidate = wlAddDays(_wlCurrentWeekStart, 7 * direction);
    var currentMonday = wlMondayOfToday();
    if (candidate > currentMonday) {
        return;  // refuse to navigate into the future
    }
    _wlCurrentWeekStart = candidate;
    wlApplyWeekHeader();
    wlLoadAllSections();
}

function wlSwitchTab(tab) {
    _wlCurrentTab = tab;
    var section = document.getElementById('page-weekly-log');
    if (!section) return;
    section.querySelectorAll('.wl-tab').forEach(function(t) {
        t.classList.remove('active');
    });
    var active = section.querySelector('[data-wltab="' + tab + '"]');
    if (active) active.classList.add('active');
    section.querySelectorAll('.wl-panel').forEach(function(p) {
        p.classList.remove('active');
    });
    var panel = document.getElementById('wlPanel-' + tab);
    if (panel) panel.classList.add('active');
}

async function wlLoadAllSections() {
    _wlLoadToken += 1;
    var token = _wlLoadToken;
    _wlSectionCache = {};

    // Reset headers/placeholders so zero-state renders don't look stale.
    wlResetSummaryStats();
    wlResetSections();

    var weekParam = '?week_start=' + encodeURIComponent(_wlCurrentWeekStart);

    // Fire all requests in parallel; each section renders independently
    // so a slow query doesn't block faster ones.
    wlLoadSummary(weekParam, token);
    wlLoadProduction(weekParam, token);
    wlLoadMaterials(weekParam, token);
    wlLoadEquipment(weekParam, token);
    wlLoadWorkOrders(weekParam, token);
    wlLoadTimeline(weekParam, token);
}

function wlResetSummaryStats() {
    var ids = [
        'wlSummaryCompleted', 'wlSummarySuccess', 'wlSummaryHours',
        'wlSummaryMaterial', 'wlSummaryWoCreated', 'wlSummaryWoCompleted',
        'wlSummaryPartsFailed', 'wlSummaryMaint'
    ];
    ids.forEach(function(id) {
        var el = document.getElementById(id);
        if (el) el.textContent = '—';
    });
}

function wlResetSections() {
    wlSetBody('wlProductionBody', 10, 'Loading…');
    wlSetBody('wlMaterialsUsageBody', 6, 'Loading…');
    var inv = document.getElementById('wlInventoryChanges');
    if (inv) inv.innerHTML = '<div class="events-empty">Loading…</div>';
    var eq = document.getElementById('wlEquipmentCards');
    if (eq) eq.innerHTML = '<div class="events-empty">Loading…</div>';
    ['wlOrdersCreated', 'wlOrdersCompleted', 'wlOrdersActive'].forEach(function(id) {
        var el = document.getElementById(id);
        if (el) el.innerHTML = '<div class="events-empty">Loading…</div>';
    });
    var parts = document.getElementById('wlPartsSummary');
    if (parts) parts.innerHTML = '';
    var tl = document.getElementById('wlTimelineBody');
    if (tl) tl.innerHTML = '<div class="events-empty">Loading…</div>';
    var tlCount = document.getElementById('wlTimelineCount');
    if (tlCount) tlCount.textContent = '0';
    var prodCount = document.getElementById('wlProductionCount');
    if (prodCount) prodCount.textContent = '0';
}

function wlSetBody(bodyId, colspan, message) {
    var body = document.getElementById(bodyId);
    if (!body) return;
    body.innerHTML = '<tr><td colspan="' + colspan + '" class="table-empty">'
        + escapeHtml(message) + '</td></tr>';
}

async function wlLoadSummary(weekParam, token) {
    try {
        var data = await apiGet('/api/reports/weekly/summary' + weekParam);
        if (token !== _wlLoadToken) return;
        _wlSectionCache.summary = data;
        wlRenderSummary(data);
    } catch (err) {
        console.error('Weekly summary error:', err);
    }
}

async function wlLoadProduction(weekParam, token) {
    try {
        var data = await apiGet('/api/reports/weekly/production' + weekParam);
        if (token !== _wlLoadToken) return;
        _wlSectionCache.production = data;
        wlRenderProduction(data);
    } catch (err) {
        wlSetBody('wlProductionBody', 10, 'Error: ' + err.message);
    }
}

async function wlLoadMaterials(weekParam, token) {
    try {
        var data = await apiGet('/api/reports/weekly/materials' + weekParam);
        if (token !== _wlLoadToken) return;
        _wlSectionCache.materials = data;
        wlRenderMaterials(data);
    } catch (err) {
        wlSetBody('wlMaterialsUsageBody', 6, 'Error: ' + err.message);
    }
}

async function wlLoadEquipment(weekParam, token) {
    try {
        var data = await apiGet('/api/reports/weekly/equipment' + weekParam);
        if (token !== _wlLoadToken) return;
        _wlSectionCache.equipment = data;
        wlRenderEquipment(data);
    } catch (err) {
        var eq = document.getElementById('wlEquipmentCards');
        if (eq) eq.innerHTML = '<div class="events-empty">Error: '
            + escapeHtml(err.message) + '</div>';
    }
}

async function wlLoadWorkOrders(weekParam, token) {
    try {
        var data = await apiGet('/api/reports/weekly/work-orders' + weekParam);
        if (token !== _wlLoadToken) return;
        _wlSectionCache.workOrders = data;
        wlRenderWorkOrders(data);
    } catch (err) {
        ['wlOrdersCreated', 'wlOrdersCompleted', 'wlOrdersActive'].forEach(function(id) {
            var el = document.getElementById(id);
            if (el) el.innerHTML = '<div class="events-empty">Error: '
                + escapeHtml(err.message) + '</div>';
        });
    }
}

async function wlLoadTimeline(weekParam, token) {
    try {
        var data = await apiGet('/api/reports/weekly/timeline' + weekParam);
        if (token !== _wlLoadToken) return;
        _wlSectionCache.timeline = data;
        wlRenderTimeline(data);
    } catch (err) {
        var body = document.getElementById('wlTimelineBody');
        if (body) body.innerHTML = '<div class="events-empty">Error: '
            + escapeHtml(err.message) + '</div>';
    }
}

function wlExportCsv() {
    if (!_wlCurrentWeekStart) return;
    var btn = document.getElementById('wlExportBtn');
    var original = btn ? btn.textContent : '';
    if (btn) {
        btn.textContent = 'Exporting…';
        btn.disabled = true;
    }
    try {
        var url = '/api/reports/weekly/export?week_start='
            + encodeURIComponent(_wlCurrentWeekStart);
        window.open(url, '_blank');
    } finally {
        setTimeout(function() {
            if (btn) {
                btn.textContent = original || 'Export CSV';
                btn.disabled = false;
            }
        }, 1200);
    }
}
