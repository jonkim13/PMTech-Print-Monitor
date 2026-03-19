// ============================================================
// Shared Globals & Utilities
// ============================================================
const POLL_INTERVAL = (
    window.APP_CONFIG &&
    Number.isFinite(Number(window.APP_CONFIG.pollIntervalMs))
) ? Number(window.APP_CONFIG.pollIntervalMs) : 3000;
let events = [];
let printerList = []; // cached for inspect picker
let notificationsEnabled = true;
const _prevPrinterStatuses = {}; // track status transitions for notifications

// =============================================================
// Navigation
// =============================================================
function switchPage(page) {
    document.querySelectorAll('.nav-item').forEach(n => n.classList.remove('active'));
    document.querySelector(`[data-page="${page}"]`).classList.add('active');

    document.querySelectorAll('.section-page').forEach(p => p.classList.remove('active'));
    document.getElementById(`page-${page}`).classList.add('active');

    const titles = { dashboard: 'Dashboard', drone: 'Drone', inventory: 'Inventory', production: 'Production Log', workorders: 'Work Orders', history: 'History' };
    document.getElementById('pageTitle').textContent = titles[page] || page;

    if(page === 'inventory') loadInventory();
    if(page === 'history') loadHistory();
    if(page === 'drone') loadDroneData();
    if(page === 'production') loadProductionData();
    if(page === 'workorders') loadWorkOrdersPage();

    document.getElementById('sidebar').classList.remove('open');
}

function toggleSidebar() {
    document.getElementById('sidebar').classList.toggle('open');
}

// ============================================================
// Formatters 
// ============================================================
function formatTime(seconds) {
    if(!seconds || seconds <= 0) return "--:--";
    const h = Math.floor(seconds / 3600);
    const m = Math.floor((seconds % 3600) / 60);
    if(h > 0) return `${h}h ${m}m`;
    return `${m}m`;
}

function formatTimestamp(isoString) {
    if(!isoString) return "";
    const d = new Date(isoString);
    return d.toLocaleTimeString([], { hour: '2-digit', minute: '2-digit' });
}

function formatDateTime(isoString) {
    if(!isoString) return "";
    const d = new Date(isoString);
    return d.toLocaleString([], { month: 'short', day: 'numeric', hour: '2-digit', minute: '2-digit' });
}

function getBadgeClass(status) {
    const map = { idle: "badge-idle", printing: "badge-printing", finished: "badge-finished", error: "badge-error", offline: "badge-offline" };
    return map[status] || "badge-unknown";
}

function getWeightClass(grams) {
    const weight = Number(grams) || 0;
    if(weight > 300) return 'weight-ok';
    if(weight > 100) return 'weight-low';
    return 'weight-critical';
}

function formatETA(remainingSec) {
    if(!remainingSec || remainingSec <= 0) return "";
    const now = new Date();
    const eta = new Date(now.getTime() + remainingSec * 1000);
    const timeStr = eta.toLocaleTimeString([], { hour: 'numeric', minute: '2-digit' });

    // Check if ETA is a different calendar day
    const nowDay = new Date(now.getFullYear(), now.getMonth(), now.getDate());
    const etaDay = new Date(eta.getFullYear(), eta.getMonth(), eta.getDate());
    const dayDiff = Math.round((etaDay - nowDay) / 86400000);
    if(dayDiff === 0) return `Done at ~${timeStr}`;
    if(dayDiff === 1) return `Done at ~${timeStr} tomorrow`;
    return `Done at ~${timeStr} (+${dayDiff}d)`;
}

function escapeHtml(value) {
    const str = String(value ?? "");
    return str
        .replaceAll("&", "&amp;")
        .replaceAll("<", "&lt;")
        .replaceAll(">", "&gt;")
        .replaceAll('"', "&quot;")
        .replaceAll("'", "&#39;");
}

// ============================================================
// Toast Notifications (this is just the browser notification)
// ============================================================
function showToast(message, type = 'success') {
    const container = document.getElementById('toastContainer');
    const toast = document.createElement('div');
    toast.className = `toast ${type}`;
    toast.textContent = message;
    container.appendChild(toast);
    setTimeout(() => toast.remove(), 4000);
}

// ============================================================
// Modal Helpers
// ============================================================
function showModal(id) {
    document.getElementById(id).classList.add('show');
}

function hideModal(id) {
    document.getElementById(id).classList.remove('show');
}

// ============================================================
// Clock
// ============================================================
function updateClock() {
    document.getElementById('clock').textContent =
        new Date().toLocaleTimeString([], { hour: '2-digit', minute: '2-digit', second: '2-digit' });
}
setInterval(updateClock, 1000);
updateClock();

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

// ============================================================
// Modal Close Listeners
// ============================================================
document.querySelectorAll('.modal-overlay').forEach(overlay => {
    overlay.addEventListener('click', (e) => {
        if(e.target === overlay) {
            overlay.classList.remove('show');
        }
    });
});

document.addEventListener('keydown', (e) => {
    if(e.key === 'Escape') {
        document.querySelectorAll('.modal-overlay.show').forEach(m => m.classList.remove('show'));
    }
});
