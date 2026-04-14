// ============================================================
// Work Orders - Print from Queue Modal
// ============================================================

async function showQueuePrintModal(queueId, jobId) {
    var modal = document.getElementById('queuePrintModal');
    var queueIds = Array.isArray(queueId)
        ? queueId.filter(function(value) { return value !== null && value !== undefined && value !== ''; })
        : (queueId !== null && queueId !== undefined && queueId !== '' ? [queueId] : []);
    var isJobExecution = !!jobId;
    modal.dataset.queueIds = queueIds.join(',');
    modal.dataset.jobId = jobId || '';
    document.getElementById('queuePrintTitle').textContent = isJobExecution
        ? 'Print Job #' + jobId
        : 'Print Queue Item';
    document.getElementById('queuePrintInfo').innerHTML = '';

    // Load queue item info
    try {
        var items;
        if (_woDetailQueueItems && _woDetailQueueItems.length) {
            items = _woDetailQueueItems.slice();
        } else {
            items = await apiGet('/api/queue');
        }
        var selected = [];

        if (isJobExecution) {
            selected = items.filter(function(item) {
                return String(item.job_id || '') === String(jobId);
            });
        } else {
            selected = queueIds.map(function(id) {
                return items.find(function(item) { return item.queue_id === id; });
            }).filter(function(item) {
                return !!item;
            });
        }

        if (selected.length) {
            var first = selected[0];
            var printable = isJobExecution
                ? selected.filter(function(qi) {
                    return isQueuePrintableStatus(qi.status);
                })
                : selected;
            var partLabels = printable.map(function(qi) {
                return qi.part_name + ' (' +
                    qi.sequence_number + '/' + qi.total_quantity + ')';
            });
            var materials = {};
            printable.forEach(function(qi) {
                materials[qi.material] = true;
            });

            var infoHtml = '<strong>' +
                escapeHtml((isJobExecution
                    ? 'Job #' + jobId + ' execution'
                    : selected.length + ' selected part' +
                        (selected.length === 1 ? '' : 's'))) +
                '</strong><br>';

            if (isJobExecution) {
                infoHtml += escapeHtml(printable.length + ' printable part' +
                    (printable.length === 1 ? '' : 's') +
                    ' will start as one execution') + '<br>';
                if (partLabels.length) {
                    infoHtml += escapeHtml(partLabels.join(', ')) + '<br>';
                }
            } else if (partLabels.length) {
                infoHtml += escapeHtml(partLabels.join(', ')) + '<br>';
            }

            infoHtml += 'Customer: ' +
                escapeHtml(first.customer_name) + ' | WO: ' +
                escapeHtml(first.wo_id);
            if (first.job_id) {
                infoHtml += ' | WO Job: #' + escapeHtml(String(first.job_id));
            }

            var materialList = Object.keys(materials);
            if (materialList.length === 1) {
                infoHtml += ' | Material: ' + escapeHtml(materialList[0]);
            } else if (materialList.length > 1) {
                infoHtml += '<br>Materials: ' +
                    escapeHtml(materialList.join(', '));
            }

            document.getElementById('queuePrintInfo').innerHTML = infoHtml;
        }
    } catch (e) { /* ignore */ }

    // Load idle printers
    var printerSel = document.getElementById('queuePrintPrinter');
    printerSel.innerHTML = '<option value="">Loading...</option>';

    try {
        var printers = await apiGet('/api/printers');
        var idle = printers.filter(function(p) {
            return p.status === 'idle' || p.status === 'finished';
        });

        if (idle.length === 0) {
            printerSel.innerHTML = '<option value="">No idle printers available</option>';
        } else {
            printerSel.innerHTML = '<option value="">-- Select printer --</option>' +
                idle.map(function(p) {
                    return '<option value="' + escapeHtml(p.printer_id) + '">' +
                        escapeHtml(p.name) + ' (' + escapeHtml(p.model) + ') — ' +
                        escapeHtml(p.status) + '</option>';
                }).join('');
        }
    } catch (e) {
        printerSel.innerHTML = '<option value="">Error loading printers</option>';
    }

    document.getElementById('queuePrintFile').value = '';
    document.getElementById('queuePrintOperatorInitials').value = '';
    showModal('queuePrintModal');
}

async function submitQueuePrint() {
    var modal = document.getElementById('queuePrintModal');
    var queueIds = (modal.dataset.queueIds || '')
        .split(',')
        .map(function(value) { return value.trim(); })
        .filter(function(value) { return !!value; });
    var jobId = modal.dataset.jobId || '';
    var printerId = document.getElementById('queuePrintPrinter').value;
    var fileInput = document.getElementById('queuePrintFile');
    var operatorInput = document.getElementById('queuePrintOperatorInitials');
    var operatorInitials = operatorInput.value.trim();

    if (!queueIds.length && !jobId) {
        showToast('Please select at least one part', 'error');
        return;
    }
    if (!printerId) {
        showToast('Please select a printer', 'error');
        return;
    }
    if (!fileInput.files.length) {
        showToast('Please select a GCode file', 'error');
        return;
    }
    if (!operatorInitials) {
        showToast('Operator initials are required to start a print', 'error');
        operatorInput.focus();
        return;
    }

    var btn = document.getElementById('queuePrintBtn');
    btn.textContent = 'Uploading...';
    btn.disabled = true;

    try {
        var formData = new FormData();
        formData.append('printer_id', printerId);
        formData.append('file', fileInput.files[0]);
        formData.append('operator_initials', operatorInitials);
        if (jobId) {
            formData.append('job_id', jobId);
        }
        queueIds.forEach(function(queueId) {
            formData.append('queue_ids', queueId);
        });

        var result = await apiPostForm('/api/queue/print', formData);
        showToast(result.message || 'Print started');
        if (result.auto_created_job === true && result.job_id) {
            showToast(
                'Job #' + result.job_id +
                ' was automatically created for this print'
            );
        }
        hideModal('queuePrintModal');
        clearWoSelection();
        if (_woDetailId) {
            var detailPanel = document.getElementById('woPanel-detail');
            if (detailPanel && detailPanel.classList.contains('active')) {
                viewWorkOrder(_woDetailId);
            }
        }
        loadQueue();
        loadQueueStats();
    } catch (e) {
        showToast('Error: ' + e.message, 'error');
    } finally {
        btn.textContent = 'Send & Print';
        btn.disabled = false;
    }
}
