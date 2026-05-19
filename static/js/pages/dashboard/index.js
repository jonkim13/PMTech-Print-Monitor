// ============================================================
// Dashboard bootstrap.
// All render + poll logic lives in poll.js.
// switchPage() handles start/stop on tab change.
// Guard: this script is loaded by base.html on every page,
// including standalone routes (e.g. /work-orders/<id>) that
// don't have a Dashboard section. Don't auto-poll there.
// ============================================================

document.addEventListener('DOMContentLoaded', function () {
    if (!document.getElementById('page-dashboard')) return;
    startDashboardPoll();
});
