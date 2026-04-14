// ============================================================
// Weekly Log - Summary stat cards renderer
// ============================================================

function wlSetText(id, value) {
    var el = document.getElementById(id);
    if (el) el.textContent = value;
}

function wlRenderSummary(data) {
    var prod = (data && data.production) || {};
    var mat = (data && data.materials) || {};
    var wo = (data && data.work_orders) || {};
    var eq = (data && data.equipment) || {};

    wlSetText('wlSummaryCompleted', wlNumberOrZero(prod.prints_completed));
    wlSetText(
        'wlSummarySuccess',
        wlPercentLabel(prod.success_rate, prod.total_prints)
    );
    wlSetText(
        'wlSummaryHours',
        wlNumberOrZero(prod.total_print_hours).toFixed(1) + 'h'
    );
    wlSetText(
        'wlSummaryMaterial',
        wlFormatGrams(mat.total_grams_consumed)
    );
    wlSetText('wlSummaryWoCreated', wlNumberOrZero(wo.created));
    wlSetText('wlSummaryWoCompleted', wlNumberOrZero(wo.completed));
    wlSetText('wlSummaryPartsFailed', wlNumberOrZero(wo.parts_failed));
    wlSetText('wlSummaryMaint', wlNumberOrZero(eq.maintenance_events));
}

function wlNumberOrZero(value) {
    var n = Number(value);
    return Number.isFinite(n) ? n : 0;
}

function wlPercentLabel(rate, total) {
    var totalNum = wlNumberOrZero(total);
    if (!totalNum) return '—';
    var n = wlNumberOrZero(rate);
    return n.toFixed(1) + '%';
}

function wlFormatGrams(grams) {
    var n = wlNumberOrZero(grams);
    if (!n) return '0g';
    if (n >= 1000) {
        return (n / 1000).toFixed(1) + 'kg';
    }
    return Math.round(n) + 'g';
}
