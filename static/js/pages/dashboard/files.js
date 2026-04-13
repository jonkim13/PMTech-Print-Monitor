// ============================================================
// Dashboard - Files modal, storage parsing
// ============================================================

async function showFilesModal(printerId, printerName) {
    document.getElementById('filesModalTitle').textContent = printerName;
    document.getElementById('filesList').innerHTML = '<div class="events-empty">Loading files...</div>';
    showModal('filesModal');

    try {
        const resp = await fetch(`/api/printers/${printerId}/files`);
        const data = await resp.json();

        if(data.error) {
            document.getElementById('filesList').innerHTML =
                `<div class="events-empty">${escapeHtml(data.error)}</div>`;
            return;
        }

        const files = extractFilesFromStorageResponse(data);

        if(files.length === 0) {
            const storageNames = extractStorageNames(data);
            const storageText = storageNames.length
                ? `Mounted storage: ${storageNames.map(escapeHtml).join(", ")}`
                : "No mounted storage detected from printer API";
            document.getElementById('filesList').innerHTML =
                `<div class="events-empty">No files found.<br><br>${storageText}</div>`;
            return;
        }

        document.getElementById('filesList').innerHTML = files.map(f => {
            const name = escapeHtml(f.display_name || f.name || f.filename || f.path || 'Unknown');
            const sizeKB = f.size ? (f.size / 1024).toFixed(0) + ' KB' : '';
            return `<div class="file-item">
                <span class="file-name">${name}</span>
                <span class="file-size">${sizeKB}</span>
            </div>`;
        }).join('');
    } catch (e) {
        document.getElementById('filesList').innerHTML =
            `<div class="events-empty">Error: ${escapeHtml(e.message)}</div>`;
    }
}

function extractStorageNames(data) {
    if(!data || typeof data !== 'object') {
        return [];
    }

    if(Array.isArray(data.storage_list)) {
        return data.storage_list
            .map(s => s.storage || s.name || s.path || '')
            .filter(Boolean);
    }

    return [];
}

function extractFilesFromStorageResponse(data) {
    const files = [];
    const walk = (node, prefix = '') => {
        if(!node || typeof node !== 'object') {
            return;
        }

        const type = String(node.type || '').toUpperCase();
        const name = String(node.display_name || node.name || node.filename || '');
        const path = String(node.path || '').trim();
        const fullPath = path || [prefix, name].filter(Boolean).join('/');

        if(type === 'FILE' || type === 'PRINT_FILE') {
            files.push({
                name,
                display_name: node.display_name || node.name || node.filename || fullPath,
                filename: node.filename || node.name || node.display_name || '',
                path: fullPath,
                size: Number(node.size) || 0
            });
        }

        const childKeys = ['children', 'files', 'items'];
        for(const key of childKeys) {
            if(Array.isArray(node[key])) {
                for (const child of node[key]) {
                    walk(child, fullPath || prefix);
                }
            }
        }
    };

    if(Array.isArray(data)) {
        data.forEach(item => walk(item));
        return files;
    }

    if(data && typeof data === 'object') {
        if(Array.isArray(data.storage_list)) {
            for(const storage of data.storage_list) {
                walk(storage);
            }
        } else {
            walk(data);
        }
    }

    // Deduplicate by path/name to avoid repeats when multiple trees include refs.
    const seen = new Set();
    return files.filter(f => {
        const key = `${f.path}|${f.name}`;

        if(seen.has(key)) {
            return false;
        }

        seen.add(key);

        return true;
    });
}
