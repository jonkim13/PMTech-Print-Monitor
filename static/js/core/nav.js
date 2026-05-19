// ============================================================
// Core - Navigation, browser notifications
// ============================================================

function switchPage(page) {
    document.querySelectorAll('.side-item').forEach(n => n.classList.remove('active'));
    const navBtn = document.querySelector(`.side-item[data-page="${page}"]`);
    if (navBtn) navBtn.classList.add('active');

    document.querySelectorAll('.section-page').forEach(p => p.classList.remove('active'));
    const section = document.getElementById(`page-${page}`);
    if (section) section.classList.add('active');

    const titles = {
        dashboard: 'Dashboard',
        workorders: 'Work Orders',
        inventory: 'Inventory',
        production: 'Production',
        history: 'History',
        'weekly-log': 'Weekly Log'
    };
    const titleEl = document.getElementById('pageTitle');
    if (titleEl) titleEl.textContent = titles[page] || page;

    const livePill = document.getElementById('topbarLivePill');
    if (livePill) livePill.style.display = page === 'dashboard' ? '' : 'none';

    if (page === 'dashboard') {
        if (typeof startDashboardPoll === 'function') startDashboardPoll();
    } else if (typeof stopDashboardPoll === 'function') {
        stopDashboardPoll();
    }

    if(page === 'inventory') loadInventory();
    if(page === 'history') loadHistory();
    if(page === 'production') loadProductionData();
    if(page === 'workorders') loadWorkOrdersPage();
    if(page === 'weekly-log') loadWeeklyLog();

    if (page !== 'workorders') {
        if (typeof stopWoQueueAutoRefresh === 'function') {
            stopWoQueueAutoRefresh();
        }
        if (typeof stopWoDetailAutoRefresh === 'function') {
            stopWoDetailAutoRefresh();
        }
    }

    const sidebar = document.getElementById('sidebar');
    if (sidebar) sidebar.classList.remove('open');
}

function updateSidebarAttnBadge(count) {
    const badge = document.getElementById('sidebarAttnBadge');
    if (!badge) return;
    const n = Number(count) || 0;
    if (n > 0) {
        badge.textContent = n;
        badge.style.display = '';
    } else {
        badge.style.display = 'none';
    }
    const bdot = document.getElementById('topbarBellDot');
    if (bdot) bdot.style.display = n > 0 ? '' : 'none';
}

function toggleSidebar() {
    document.getElementById('sidebar').classList.toggle('open');
}

// ============================================================
// Browser Notifications
// ============================================================
function initNotifications() {
    if(!('Notification' in window)) {
        return;
    }

    if(Notification.permission === 'default') {
        Notification.requestPermission().then(updateBellIcon);
    }
    updateBellIcon();
}

function updateBellIcon() {
    const bell = document.getElementById('notifBell');
    if (!bell) {
        return;
    }

    const allowed = 'Notification' in window && Notification.permission === 'granted';
    if (!allowed || !notificationsEnabled) {
        bell.classList.add('disabled');
        bell.title = !allowed ? 'Notifications blocked by browser' : 'Notifications off (click to enable)';
    } else {
        bell.classList.remove('disabled');
        bell.title = 'Notifications on (click to disable)';
    }
}

function toggleNotifications() {
    if(!('Notification' in window)) {
        showToast('Browser does not support notifications', 'error');
        return;
    }
    if(Notification.permission === 'default') {
        Notification.requestPermission().then(perm => {
            notificationsEnabled = perm === 'granted';
            updateBellIcon();
        });
        return;
    }
    if(Notification.permission === 'denied') {
        showToast('Notifications blocked — enable in browser settings', 'error');
        return;
    }
    notificationsEnabled = !notificationsEnabled;
    updateBellIcon();
    showToast(notificationsEnabled ? 'Notifications enabled' : 'Notifications disabled');
}

function playBeep(type) {
    try {
        const ctx = new (window.AudioContext || window.webkitAudioContext)();
        const osc = ctx.createOscillator();
        const gain = ctx.createGain();
        osc.connect(gain);
        gain.connect(ctx.destination);
        gain.gain.value = 0.15;

        if(type === 'complete') {
            // Two-tone chime
            osc.frequency.value = 880;
            osc.type = 'sine';
            osc.start();
            osc.frequency.setValueAtTime(1100, ctx.currentTime + 0.15);
            gain.gain.setValueAtTime(0.15, ctx.currentTime + 0.25);
            gain.gain.linearRampToValueAtTime(0, ctx.currentTime + 0.4);
            osc.stop(ctx.currentTime + 0.4);
        } else {
            // Low buzz for errors
            osc.frequency.value = 330;
            osc.type = 'square';
            osc.start();
            gain.gain.setValueAtTime(0.12, ctx.currentTime + 0.2);
            gain.gain.linearRampToValueAtTime(0, ctx.currentTime + 0.5);
            osc.stop(ctx.currentTime + 0.5);
        }
    } catch (_) { /* AudioContext not available */ }
}

function sendBrowserNotification(title, body, type) {
    if(!notificationsEnabled) {
        return;
    }

    if(!('Notification' in window) || Notification.permission !== 'granted') {
        return;
    }

    try { new Notification(title, { body, icon: '', tag: title + Date.now() }); } catch (_) {}
    playBeep(type);
}

function checkPrinterTransitions(printers) {
    for(const p of printers) {
        const pid = p.printer_id;
        const newStatus = String(p.status || 'unknown').toLowerCase();
        const prev = _prevPrinterStatuses[pid];
        _prevPrinterStatuses[pid] = newStatus;

        // Skip the first poll (no previous data to compare)
        if(prev === undefined) continue;
        if(prev === newStatus) continue;

        const name = p.name || pid;
        const filename = (p.job && p.job.filename) || 'a job';

        if(prev === 'printing' && (newStatus === 'finished' || newStatus === 'idle')) {
            sendBrowserNotification(
                'Print Complete',
                `${name} has finished printing ${filename}`,
                'complete'
            );
        } else if (newStatus === 'error') {
            sendBrowserNotification(
                'Printer Error',
                `${name} encountered an error`,
                'error'
            );
        }
    }
}

// Request permission on page load
initNotifications();
