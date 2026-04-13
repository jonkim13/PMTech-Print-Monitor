// ============================================================
// Core - Shared global state
// ============================================================
const POLL_INTERVAL = (
    window.APP_CONFIG &&
    Number.isFinite(Number(window.APP_CONFIG.pollIntervalMs))
) ? Number(window.APP_CONFIG.pollIntervalMs) : 3000;
var events = [];
var printerList = [];
var notificationsEnabled = true;
var _prevPrinterStatuses = {};
