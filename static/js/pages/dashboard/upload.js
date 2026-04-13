// ============================================================
// Dashboard - Upload GCode modal
// ============================================================

function showUploadModal(printerId, printerName) {
    document.getElementById('uploadPrinterName').textContent = printerName;
    document.getElementById('uploadModal').dataset.printerId = printerId;
    document.getElementById('gcodeFile').value = '';
    document.getElementById('printAfterUpload').checked = false;
    document.getElementById('uploadOperatorInitials').value = '';
    toggleUploadOperatorInitials();
    showModal('uploadModal');
}

function toggleUploadOperatorInitials() {
    const printAfter = document.getElementById('printAfterUpload').checked;
    const group = document.getElementById('uploadOperatorGroup');
    const input = document.getElementById('uploadOperatorInitials');

    group.style.display = printAfter ? 'block' : 'none';
    input.disabled = !printAfter;
    input.required = printAfter;
    if(!printAfter) {
        input.value = '';
    }
}

async function submitUpload() {
    const printerId = document.getElementById('uploadModal').dataset.printerId;
    const fileInput = document.getElementById('gcodeFile');
    const printAfter = document.getElementById('printAfterUpload').checked;
    const operatorInput = document.getElementById('uploadOperatorInitials');
    const operatorInitials = operatorInput.value.trim();

    if(!fileInput.files.length) {
        showToast('Please select a GCode file', 'error');
        return;
    }
    if(printAfter && !operatorInitials) {
        showToast('Operator initials are required to start a print', 'error');
        operatorInput.focus();
        return;
    }

    const file = fileInput.files[0];
    const formData = new FormData();
    formData.append('file', file);
    if(printAfter) {
        formData.append('operator_initials', operatorInitials);
    }

    const btn = document.getElementById('uploadBtn');
    btn.textContent = 'Uploading to server...';
    btn.disabled = true;

    try {
        const url = '/api/printers/' + printerId + '/upload' + (printAfter ? '?print_after=1' : '');
        btn.textContent = printAfter ? 'Uploading, verifying, then starting...' : 'Uploading to printer...';
        const resp = await fetch(url, { method: 'POST', body: formData });
        const result = await resp.json();

        if(result.success) {
            showToast(result.message || ('Uploaded ' + file.name + ' successfully'));
            hideModal('uploadModal');
        } else {
            var errMsg = formatUploadError(result, resp.status);
            if(result.stored_on_server && (result.filename || result.upload_session_id)) {
                errMsg += ' — File saved on server, you can retry without re-uploading.';
            }
            showToast(errMsg, 'error');
        }
    } catch (e) {
        showToast('Upload error: ' + e.message, 'error');
    } finally {
        btn.textContent = 'Upload';
        btn.disabled = false;
    }
}

function formatUploadError(result, statusCode) {
    if(result && result.error_type === 'timeout') {
        return result.message || result.error || 'Upload timed out while sending the file to the printer';
    }
    if(result && result.error_type === 'verification_failed') {
        return result.message || 'The upload finished, but the file never appeared on the printer';
    }
    if(result && (result.error_type === 'start_failed' || result.error_type === 'start_timeout')) {
        return result.message || 'File uploaded, but the printer did not confirm the print start';
    }
    if(result && result.error_type === 'remote_file_missing') {
        return result.message || 'The uploaded file could not be found on the printer anymore';
    }
    if(statusCode === 400) {
        return 'Invalid upload: ' + ((result && (result.message || result.error)) || 'Check the selected file and try again.');
    }
    if(result && (result.error_type === 'printer_api_error' || result.error_type === 'printer_busy')) {
        return result.message || result.error || 'Printer upload failed';
    }
    return 'Upload failed: ' + ((result && (result.message || result.error)) ? (result.message || result.error) : 'Unknown error');
}
