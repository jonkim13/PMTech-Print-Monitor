// ============================================================
// Production Log - State, tab navigation, shared helpers
// ============================================================
let _currentJobId = null;

// ---- Sub-tab Navigation ----
function switchProdTab(tab) {
    const section = document.getElementById('page-production');
    section.querySelectorAll('.prod-tab').forEach(t => t.classList.remove('active'));
    section.querySelector(`[data-prodtab="${tab}"]`).classList.add('active');
    section.querySelectorAll('.prod-panel').forEach(p => p.classList.remove('active'));
    document.getElementById(`prodPanel-${tab}`).classList.add('active');

    if (tab === 'jobs') loadProductionJobs();
    if (tab === 'machines') loadMachineSummaries();
    if (tab === 'materials') { /* user searches manually */ }
}

// ---- Main loader called from switchPage ----
function loadProductionData() {
    populatePrinterDropdowns();
    loadProductionJobs();
}

function populatePrinterDropdowns() {
    const selectors = ['prodFilterPrinter', 'machineLogPrinter', 'maintPrinterSelect'];

    for(const id of selectors) {
        const el = document.getElementById(id);
        if(!el) continue;
        // Keep existing first option
        const firstOpt = el.options[0] ? el.options[0].outerHTML : '';
        el.innerHTML = firstOpt;

        for (const p of printerList) {
            const opt = document.createElement('option');
            opt.value = p.printer_id;
            opt.textContent = p.name || p.printer_id;
            el.appendChild(opt);
        }
    }
}

function truncate(str, len) {
    if (str.length <= len) return str;
    return str.substring(0, len) + '...';
}
