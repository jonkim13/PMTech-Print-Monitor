// ============================================================
// History - Print history table and statistics
// ============================================================
async function loadHistory() {
    try {
        const [histResp, statsResp] = await Promise.all([
            fetch('/api/history?limit=100'),
            fetch('/api/history/stats')
        ]);

        const history = await histResp.json();
        const stats = await statsResp.json();

        // Stats cards
        document.getElementById('histTotal').textContent = stats.total_events;
        document.getElementById('histCompleted').textContent = stats.completed;
        document.getElementById('histRate').textContent = stats.success_rate + '%';
        document.getElementById('histAvgDur').textContent = formatTime(stats.avg_duration_sec);

        // Per-printer stats
        const perPrinter = stats.per_printer || {};
        const ppContainer = document.getElementById('perPrinterStats');

        if(Object.keys(perPrinter).length > 0) {
            ppContainer.innerHTML = Object.entries(perPrinter).map(([name, count]) => `
                <div class="history-stat-card" style="min-width: 140px;">
                    <span class="stat-label">${escapeHtml(name)}</span>
                    <span class="stat-value purple">${Number(count) || 0}</span>
                </div>
            `).join('');
        } else {
            ppContainer.innerHTML = '<span style="color: var(--text-dim); font-size: 12px;">No completed prints yet</span>';
        }

        // History table
        document.getElementById('histCount').textContent = history.length;
        const body = document.getElementById('historyBody');

        if(history.length === 0) {
            body.innerHTML = '<tr><td colspan="5" class="table-empty">No history yet</td></tr>';
            return;
        }

        body.innerHTML = history.map(h => {
            const eventClass = h.event_type === 'print_complete' ? 'color:var(--accent-green)'
                : h.event_type === 'printer_error' ? 'color:var(--accent-red)'
                : 'color:var(--accent-blue)';
            const eventLabel = h.event_type === 'print_complete' ? 'Completed'
                : h.event_type === 'printer_error' ? 'Error'
                : h.event_type === 'print_started' ? 'Started'
                : h.event_type || 'Unknown';

            return `<tr>
                <td style="font-family: 'JetBrains Mono', monospace; font-size: 10px; color: var(--text-dim); white-space: nowrap;">${formatDateTime(h.timestamp)}</td>
                <td style="font-weight: 500;">${escapeHtml(h.printer_name)}</td>
                <td><span style="${eventClass}; font-family: 'JetBrains Mono', monospace; font-size: 11px;">${eventLabel}</span></td>
                <td style="font-family: 'JetBrains Mono', monospace; font-size: 11px; max-width: 200px; overflow: hidden; text-overflow: ellipsis; white-space: nowrap;">${escapeHtml(h.filename || '-')}</td>
                <td style="font-family: 'JetBrains Mono', monospace; font-size: 11px;">${h.duration_sec ? formatTime(h.duration_sec) : '-'}</td>
            </tr>`;
        }).join('');
    } catch (e) {
        console.error('History load error:', e);
    }
}
