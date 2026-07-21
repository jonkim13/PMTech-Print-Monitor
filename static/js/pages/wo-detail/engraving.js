/* Custom Engraving section on the WO detail page (Phase E-2).
 *
 * Fully self-contained: reads the same data-wo-id as index.js, fetches the
 * decoupled /api/work-orders/<wo_id>/engraving endpoint, renders the section,
 * and polls on its own timer only while the request is still generating. It
 * does not touch the main WO poll or any other wo-detail JS.
 *
 * WOs not created via Custom Engraving return null -> the card stays hidden.
 */
(function () {
    var POLL_MS = 2500;
    var woId = null;
    var pollTimer = null;
    var inflight = false;

    function esc(value) {
        return String(value == null ? '' : value).replace(/[&<>"']/g, function (c) {
            return { '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }[c];
        });
    }

    function stopPolling() {
        if (pollTimer) { clearInterval(pollTimer); pollTimer = null; }
    }

    function renderGenerating(view) {
        return ''
            + '<div class="muted" style="display:flex;align-items:center;gap:8px;">'
            + '<i data-lucide="loader" class="icon icon-sm"></i>'
            + '<span>Generating engraved model for <strong>' + esc(view.product_display) + '</strong>'
            + ' (x' + esc(view.quantity) + ')&hellip; this can take a minute.</span>'
            + '</div>';
    }

    function renderReady(view) {
        var tc = view.triangle_counts || {};
        return ''
            + '<div style="display:flex;gap:16px;flex-wrap:wrap;align-items:flex-start;">'
            + '  <div>'
            + '    <img src="' + esc(view.preview_prod_url) + '" alt="Engraved product preview"'
            + '         style="width:240px;max-width:100%;border:1px solid var(--border,#ddd);border-radius:8px;background:#fff;">'
            + '    <div class="muted" style="font-size:12px;margin-top:4px;text-align:center;">Product preview</div>'
            + '  </div>'
            + '  <div style="flex:1;min-width:200px;">'
            + '    <div class="k" style="margin-bottom:6px;">Ready</div>'
            + '    <div class="muted" style="font-size:13px;margin-bottom:10px;">'
            + '      ' + esc(view.product_display) + ' &middot; from ' + esc(view.original_filename)
            + (tc.prod ? ' &middot; ' + esc(tc.prod) + ' triangles' : '')
            + '    </div>'
            + '    <div style="display:flex;gap:8px;flex-wrap:wrap;">'
            + '      <a class="btn sm primary" href="' + esc(view.stl_prod_url) + '" download>'
            + '        <i data-lucide="download" class="icon icon-sm"></i> Product STL</a>'
            + '      <a class="btn sm" href="' + esc(view.stl_mold_url) + '" download>'
            + '        <i data-lucide="download" class="icon icon-sm"></i> Mold STL</a>'
            + '    </div>'
            + '  </div>'
            + '</div>';
    }

    function renderFailed(view) {
        return ''
            + '<div class="k" style="margin-bottom:6px;color:var(--danger,#c0392b);">Generation failed</div>'
            + '<div class="muted" style="font-size:13px;">' + esc(view.error_message || 'Unknown error.') + '</div>'
            + '<div class="muted" style="font-size:12px;margin-top:6px;">'
            + 'The work order was still created and can be used normally.</div>';
    }

    function render(view) {
        var card = document.getElementById('wo-engraving-card');
        var body = document.getElementById('wo-engraving-body');
        if (!card || !body) return;
        if (!view) { card.style.display = 'none'; stopPolling(); return; }

        card.style.display = '';
        if (view.status === 'ready') {
            body.innerHTML = renderReady(view);
            stopPolling();
        } else if (view.status === 'failed') {
            body.innerHTML = renderFailed(view);
            stopPolling();
        } else {
            body.innerHTML = renderGenerating(view);
            ensurePolling();
        }
        if (window.lucide && window.lucide.createIcons) { window.lucide.createIcons(); }
    }

    async function fetchOnce() {
        if (inflight) return;
        inflight = true;
        try {
            var view = await apiGet('/api/work-orders/' + encodeURIComponent(woId) + '/engraving');
            render(view);
        } catch (e) {
            /* transient error — leave the current view, try again next tick */
        } finally {
            inflight = false;
        }
    }

    function ensurePolling() {
        if (pollTimer) return;
        pollTimer = setInterval(fetchOnce, POLL_MS);
    }

    function init() {
        var section = document.getElementById('page-wo-detail');
        if (!section) return;
        woId = section.getAttribute('data-wo-id');
        if (!woId) return;
        fetchOnce();
    }

    if (document.readyState === 'loading') {
        document.addEventListener('DOMContentLoaded', init);
    } else {
        init();
    }
})();
