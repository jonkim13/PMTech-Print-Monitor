// ============================================================
// Drone - Status display, mission controls, mission log
// ============================================================

async function loadDroneData() {
    try {
        const [statusResp, missionsResp] = await Promise.all([
            fetch('/api/drone/status'),
            fetch('/api/drone/missions')
        ]);
        const status = await statusResp.json();
        const missions = await missionsResp.json();

        // Update status display
        const connected = status.connected;
        const connEl = document.getElementById('droneConnected');
        connEl.textContent = connected ? 'ONLINE' : 'OFFLINE';
        connEl.style.color = connected ? 'var(--accent-green)' : 'var(--accent-red)';

        document.getElementById('droneBattery').textContent = status.battery_percent + '%';
        document.getElementById('droneState').textContent = (status.state || 'unknown').toUpperCase();

        const pos = status.position || { x: 0, y: 0, z: 0 };
        document.getElementById('dronePosition').textContent = `${pos.x}, ${pos.y}, ${pos.z}`;
        document.getElementById('droneMission').textContent = status.current_mission || 'None';

        // Update mission log
        document.getElementById('missionCount').textContent = missions.length;
        if(missions.length > 0) {
            document.getElementById('missionLog').innerHTML = missions.map(m => `
                <div class="mission-item">
                    <div>
                        <span class="mission-type">${escapeHtml(m.type || 'unknown')}</span>
                        ${m.target ? ` → ${escapeHtml(m.target)}` : ''}
                    </div>
                    <div style="display: flex; align-items: center; gap: 8px;">
                        <span class="mission-status">${escapeHtml(m.status)}</span>
                        <span class="mission-time">${formatTimestamp(m.timestamp)}</span>
                    </div>
                </div>
            `).join('');
        } else {
            document.getElementById('missionLog').innerHTML =
                '<div class="events-empty">No missions yet</div>';
        }

        // Populate inspect picker if needed
        if(printerList.length > 0) {
            const sel = document.getElementById('inspectTarget');
            sel.innerHTML = printerList.map(p =>
                `<option value="${escapeHtml(p.printer_id)}">${escapeHtml(p.name)}</option>`
            ).join('');
        }
    } catch (e) {
        console.error('Drone data load failed:', e);
    }
}

async function sendDroneMission(type, target) {
    try {
        const body = { type };
        if(target) body.target = target;

        const resp = await fetch('/api/drone/mission', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify(body)
        });
        const result = await resp.json();
        showToast(`Mission queued: ${type}`);
        loadDroneData();
    } catch (e) {
        showToast(`Mission error: ${e.message}`, 'error');
    }
}

function showInspectPicker() {
    document.getElementById('inspectPicker').style.display = 'block';
}

function sendInspectMission() {
    const target = document.getElementById('inspectTarget').value;
    sendDroneMission('inspect_printer', target);
    document.getElementById('inspectPicker').style.display = 'none';
}
