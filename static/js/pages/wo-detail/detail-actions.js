// ============================================================
// WO Detail page — actions module (Phase 5f split).
// Event handlers triggered from inline onclick / onchange in
// templates + JS-rendered HTML. All handlers go through the
// existing apiFetch helpers in core/api.js; on success they
// re-poll via WoDetail.poll() to refresh state.
// Every handler attached to window.WoDetail.* must remain a
// global function reachable from inline `onclick="WoDetail.foo()"`.
// ============================================================

(function () {
    var W = (window.WoDetail = window.WoDetail || {});
    W._state = W._state || {
        pollTimer: null,
        pollInflight: false,
        woId: null,
        selectedQueueIds: {},
        expandedJobs: {},
        collapsedJobs: {},
        lastSnapshot: null,
    };
    var S = W._state;

    function openCancelConfirm(title, body, onConfirm) {
        var titleEl = document.getElementById('cancelConfirmTitle');
        var bodyEl = document.getElementById('cancelConfirmBody');
        var btn = document.getElementById('cancelConfirmBtn');
        if (!titleEl || !bodyEl || !btn) {
            if (window.confirm(title + '\n\n' + body)) onConfirm();
            return;
        }
        titleEl.textContent = title;
        bodyEl.textContent = body;
        btn.onclick = function () {
            hideModal('cancelConfirmModal');
            onConfirm();
        };
        showModal('cancelConfirmModal');
    }

    function getSelectedQueueIds() {
        return Object.keys(S.selectedQueueIds)
            .map(function (k) { return parseInt(k, 10); })
            .filter(function (n) { return !isNaN(n); });
    }

    function poll() {
        if (W.poll) return W.poll();
    }

    // ------------------------------------------------------------
    // Selection state
    // ------------------------------------------------------------

    W.togglePartSelection = function (queueId, checked) {
        var key = String(queueId);
        if (checked) S.selectedQueueIds[key] = true;
        else delete S.selectedQueueIds[key];
        if (W._render && W._render.selectionToolbar) {
            W._render.selectionToolbar();
        }
    };

    W.clearSelection = function () {
        S.selectedQueueIds = {};
        document.querySelectorAll('.wo-part-select').forEach(function (input) {
            input.checked = false;
        });
        if (W._render && W._render.selectionToolbar) {
            W._render.selectionToolbar();
        }
    };

    // ------------------------------------------------------------
    // Expand / collapse
    // ------------------------------------------------------------

    W.toggleJob = function (jobId) {
        var card = document.querySelector(
            '.job-card[data-job-id="' + jobId + '"]'
        );
        if (!card) return;
        var body = card.querySelector('.job-card-body');
        var head = card.querySelector('.job-card-caret');
        var willExpand = !card.classList.contains('job-card-expanded');
        card.classList.toggle('job-card-expanded', willExpand);
        if (body) body.style.display = willExpand ? '' : 'none';
        if (head) {
            head.innerHTML = '<i data-lucide="' +
                (willExpand ? 'chevron-down' : 'chevron-right') +
                '" class="icon icon-sm"></i>';
            refreshIcons(head);
        }
        if (willExpand) {
            S.expandedJobs[jobId] = true;
            delete S.collapsedJobs[jobId];
            if (S.lastSnapshot) {
                var job = (S.lastSnapshot.jobs || []).find(function (j) {
                    return String(j.job_id) === String(jobId);
                });
                if (job && W._render && W._render.jobBody) {
                    W._render.jobBody(card, job, S.lastSnapshot.queue_items || []);
                }
            }
        } else {
            delete S.expandedJobs[jobId];
            S.collapsedJobs[jobId] = true;
        }
    };

    // ------------------------------------------------------------
    // WO / Job level cancel & retry
    // ------------------------------------------------------------

    W.cancelWO = async function (woIdArg) {
        openCancelConfirm(
            'Cancel work order ' + woIdArg + '?',
            'Every non-completed part will be cancelled. Any active print will be stopped.',
            async function () {
                try {
                    var result = await apiDelete('/api/workorders/' + encodeURIComponent(woIdArg));
                    var n = result.cancelled_count || 0;
                    showToast('Cancelled ' + n + ' part' + (n === 1 ? '' : 's'));
                    poll();
                } catch (e) {
                    showToast('Error: ' + e.message, 'error');
                }
            }
        );
    };

    W.retryWO = async function (woIdArg) {
        openCancelConfirm(
            'Re-queue cancelled / failed parts?',
            'Every cancelled or failed part in ' + woIdArg + ' will be moved back to the queue.',
            async function () {
                try {
                    var result = await apiPost('/api/workorders/' + encodeURIComponent(woIdArg) + '/retry', {});
                    var n = result.requeued_count || 0;
                    showToast('Re-queued ' + n + ' part' + (n === 1 ? '' : 's'));
                    poll();
                } catch (e) {
                    showToast('Error: ' + e.message, 'error');
                }
            }
        );
    };

    W.cancelJob = async function (woIdArg, jobId) {
        openCancelConfirm(
            'Cancel Job #' + jobId + '?',
            'Every non-completed part in this job will be cancelled. Any active print will be stopped.',
            async function () {
                try {
                    var result = await apiDelete('/api/workorders/' +
                        encodeURIComponent(woIdArg) + '/jobs/' + jobId);
                    var n = result.cancelled_count || 0;
                    showToast('Cancelled ' + n + ' part' + (n === 1 ? '' : 's'));
                    poll();
                } catch (e) {
                    showToast('Error: ' + e.message, 'error');
                }
            }
        );
    };

    W.retryJob = async function (woIdArg, jobId) {
        openCancelConfirm(
            'Retry Job #' + jobId + ' failures?',
            'Every cancelled / failed part in this job will be re-queued.',
            async function () {
                try {
                    var result = await apiPost('/api/workorders/' +
                        encodeURIComponent(woIdArg) + '/jobs/' + jobId + '/retry', {});
                    var n = result.requeued_count || 0;
                    showToast('Re-queued ' + n + ' part' + (n === 1 ? '' : 's'));
                    poll();
                } catch (e) {
                    showToast('Error: ' + e.message, 'error');
                }
            }
        );
    };

    // ------------------------------------------------------------
    // Part level
    // ------------------------------------------------------------

    W.cancelPart = async function (queueId, partName) {
        openCancelConfirm(
            'Cancel ' + (partName || 'this part') + '?',
            'If the part is currently printing, the printer will be stopped.',
            async function () {
                try {
                    await apiPost('/api/queue/' + queueId + '/cancel', {});
                    showToast('Cancelled');
                    poll();
                } catch (e) {
                    showToast('Error: ' + e.message, 'error');
                }
            }
        );
    };

    W.printPart = function (queueId, jobId) {
        // Reuse the existing Print modal; pre-stuff its expected
        // _woDetailQueueItems global from our snapshot so it can show
        // the part metadata without hitting a (now-gone) /api/queue.
        var snapshot = S.lastSnapshot || window.WO_DETAIL_INITIAL;
        if (snapshot && typeof window._woDetailQueueItems !== 'undefined') {
            window._woDetailQueueItems = snapshot.queue_items || [];
            window._woDetailId = snapshot.wo_id;
        }
        if (typeof showQueuePrintModal !== 'function') {
            showToast('Print modal unavailable', 'error');
            return;
        }
        showQueuePrintModal(queueId, jobId || '');
    };

    W.printJob = function (jobId) {
        var snapshot = S.lastSnapshot || window.WO_DETAIL_INITIAL;
        if (snapshot && typeof window._woDetailQueueItems !== 'undefined') {
            window._woDetailQueueItems = snapshot.queue_items || [];
            window._woDetailId = snapshot.wo_id;
        }
        showQueuePrintModal([], jobId);
    };

    W.printSelected = function () {
        var queueIds = getSelectedQueueIds();
        if (!queueIds.length) {
            showToast('Select at least one part to print', 'error');
            return;
        }
        var snapshot = S.lastSnapshot || window.WO_DETAIL_INITIAL;
        var queueItems = (snapshot && snapshot.queue_items) || [];
        var jobIds = {};
        queueItems.filter(function (qi) {
            return S.selectedQueueIds[String(qi.queue_id)];
        }).forEach(function (qi) {
            if (qi.job_id) jobIds[String(qi.job_id)] = true;
        });
        var jobIdList = Object.keys(jobIds);
        if (jobIdList.length > 1) {
            showToast('Selected parts span multiple jobs. Pick parts from one job.', 'error');
            return;
        }
        if (snapshot && typeof window._woDetailQueueItems !== 'undefined') {
            window._woDetailQueueItems = queueItems;
            window._woDetailId = snapshot.wo_id;
        }
        showQueuePrintModal(queueIds, jobIdList[0] || '');
    };

    W.groupIntoNewJob = async function (woIdArg) {
        var queueIds = getSelectedQueueIds();
        if (!queueIds.length) {
            showToast('Select at least one part', 'error');
            return;
        }
        try {
            var result = await apiPost('/api/workorders/' + encodeURIComponent(woIdArg) + '/jobs',
                { queue_ids: queueIds });
            if (result.job) {
                showToast('Job #' + result.job.job_id + ' created with ' +
                    (result.assigned_count || queueIds.length) + ' part' +
                    ((result.assigned_count || queueIds.length) === 1 ? '' : 's'));
                W.clearSelection();
                poll();
            } else {
                showToast('Error: Unknown response', 'error');
            }
        } catch (e) {
            showToast('Error: ' + e.message, 'error');
        }
    };

    W.createJobFromSelected = function (woIdArg) {
        return W.groupIntoNewJob(woIdArg);
    };

    W.setQC = function (printJobId, queueId) {
        if (!printJobId || printJobId === 'null') {
            showToast('Cannot inspect — no production record linked to this part', 'error');
            return;
        }
        var modal = document.getElementById('woQcModal');
        if (!modal) {
            showToast('QC modal unavailable', 'error');
            return;
        }
        modal.dataset.printJobId = printJobId;
        modal.dataset.queueId = queueId || '';
        var outcomeEl = document.getElementById('woQcOutcome');
        var operatorEl = document.getElementById('woQcOperator');
        var notesEl = document.getElementById('woQcNotes');
        if (outcomeEl) outcomeEl.value = 'pass';
        if (operatorEl) operatorEl.value = '';
        if (notesEl) notesEl.value = '';

        var snapshot = S.lastSnapshot || window.WO_DETAIL_INITIAL;
        var queueItems = (snapshot && snapshot.queue_items) || [];
        var qi = queueItems.find(function (item) {
            return item.print_job_id === printJobId;
        });
        if (qi) {
            if (outcomeEl && qi.production_outcome &&
                qi.production_outcome !== 'unknown') {
                outcomeEl.value = qi.production_outcome;
            }
            if (operatorEl && qi.production_operator) {
                operatorEl.value = qi.production_operator;
            }
            if (notesEl && qi.production_notes) {
                notesEl.value = qi.production_notes;
            }
        }
        showModal('woQcModal');
    };

    W.retryPart = async function (queueId) {
        try {
            var result = await apiPost('/api/queue/' + queueId + '/retry', {});
            showToast(result.message || 'Retry sent to printer');
            poll();
        } catch (e) {
            showToast('Error: ' + e.message, 'error');
        }
    };

    // ------------------------------------------------------------
    // submitWoQc — called from the WO QC modal partial via inline
    // onclick. Lives on window so the modal markup can find it.
    // ------------------------------------------------------------
    if (typeof window.submitWoQc !== 'function') {
        window.submitWoQc = async function () {
            var modal = document.getElementById('woQcModal');
            if (!modal) return;
            var printJobId = parseInt(modal.dataset.printJobId, 10);
            if (!printJobId) {
                showToast('No print job selected', 'error');
                return;
            }
            var outcome = document.getElementById('woQcOutcome').value;
            var operator = document.getElementById('woQcOperator').value.trim();
            var notes = document.getElementById('woQcNotes').value.trim();
            if (!operator) {
                showToast('Inspector name is required', 'error');
                return;
            }
            try {
                await apiPatch('/api/production/jobs/' + printJobId, {
                    outcome: outcome, operator: operator, notes: notes
                });
                showToast('QC saved');
                hideModal('woQcModal');
                if (W && typeof W.poll === 'function') {
                    W.poll();
                }
            } catch (e) {
                showToast('Error: ' + e.message, 'error');
            }
        };
    }
})();
