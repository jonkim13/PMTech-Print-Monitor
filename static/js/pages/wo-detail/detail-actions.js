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

    // ------------------------------------------------------------
    // Phase C — Create External / Design Job modal.
    // Trigger buttons (#wo-create-external-job-btn,
    // #wo-create-design-job-btn) pass the wo_id + pre-selected type.
    // The wo_id is stashed on the dialog's dataset so submit can
    // read it without a closure variable (mirrors woQcModal's
    // dataset.printJobId pattern).
    // ------------------------------------------------------------

    function _cnijFieldsetToggle(jobType) {
        var ext = document.getElementById('cnij-external-fields');
        var des = document.getElementById('cnij-design-fields');
        if (ext) ext.hidden = (jobType !== 'External');
        if (des) des.hidden = (jobType !== 'Design');
    }

    function _cnijSetError(message) {
        var box = document.getElementById('cnij-error');
        if (!box) return;
        if (message) {
            box.textContent = message;
            box.hidden = false;
        } else {
            box.textContent = '';
            box.hidden = true;
        }
    }

    function _cnijClearInputs() {
        ['cnij-vendor', 'cnij-process',
         'cnij-designer', 'cnij-requirements'].forEach(function (id) {
            var el = document.getElementById(id);
            if (el) el.value = '';
        });
    }

    W.openNonInternalJobModal = function (woIdArg, preselectedType) {
        var modal = document.getElementById('create-non-internal-job-modal');
        if (!modal) {
            showToast('Create-Job modal unavailable', 'error');
            return;
        }
        var typeEl = document.getElementById('cnij-type');
        var jobType = (preselectedType === 'Design') ? 'Design' : 'External';
        if (typeEl) typeEl.value = jobType;
        modal.dataset.woId = woIdArg || '';

        _cnijClearInputs();
        _cnijSetError('');
        _cnijFieldsetToggle(jobType);
        showModal('create-non-internal-job-modal');
    };

    W.submitNonInternalJob = async function () {
        var modal = document.getElementById('create-non-internal-job-modal');
        if (!modal) return;
        var woIdArg = modal.dataset.woId || '';
        if (!woIdArg) {
            _cnijSetError('Missing work order id.');
            return;
        }

        var jobType = (document.getElementById('cnij-type') || {}).value
            || 'External';
        var body = { job_type: jobType };

        if (jobType === 'External') {
            var vendor = (document.getElementById('cnij-vendor') || {}).value || '';
            var process = (document.getElementById('cnij-process') || {}).value || '';
            vendor = vendor.trim();
            process = process.trim();
            if (!vendor) {
                _cnijSetError('Vendor is required.');
                return;
            }
            if (!process) {
                _cnijSetError('Process is required.');
                return;
            }
            body.vendor = vendor;
            body.external_process = process;
        } else if (jobType === 'Design') {
            var designer = (document.getElementById('cnij-designer') || {}).value || '';
            var requirements = (document.getElementById('cnij-requirements') || {}).value || '';
            designer = designer.trim();
            requirements = requirements.trim();
            if (!designer) {
                _cnijSetError('Designer is required.');
                return;
            }
            body.designer = designer;
            if (requirements) body.requirements = requirements;
        } else {
            _cnijSetError('Unsupported job type: ' + jobType);
            return;
        }

        _cnijSetError('');
        try {
            var result = await apiPost(
                '/api/workorders/' + encodeURIComponent(woIdArg) + '/jobs',
                body
            );
            var jobId = result && result.job && result.job.job_id;
            showToast('Job #' + (jobId || '?') + ' created (' + jobType + ')');
            hideModal('create-non-internal-job-modal');
            poll();
        } catch (e) {
            _cnijSetError(e.message || 'Request failed');
        }
    };

    // Wire the type-select change handler once. The dialog markup is
    // server-rendered on page load (via {% include %} in
    // wo_detail.html), so we just need to wait until the DOM is
    // parsed before attaching — same lifecycle gate that index.js
    // uses for its init().
    function _wireCnijTypeListener() {
        var typeEl = document.getElementById('cnij-type');
        if (!typeEl || typeEl.dataset.cnijListenerAttached === '1') return;
        typeEl.addEventListener('change', function () {
            _cnijFieldsetToggle(typeEl.value);
        });
        typeEl.dataset.cnijListenerAttached = '1';
    }
    if (document.readyState === 'loading') {
        document.addEventListener('DOMContentLoaded', _wireCnijTypeListener);
    } else {
        _wireCnijTypeListener();
    }

    // ------------------------------------------------------------
    // Phase C — Non-Internal job lifecycle + inline-edit.
    // External/Design cards render editable field rows with a
    // pencil button per field. The button calls editExternal/Design,
    // which finds the [data-field] span on the matching card and
    // hands it off to _inlineEdit. _inlineEdit swaps the span for
    // an input + save/cancel buttons and PATCHes via the caller's
    // onSave on commit.
    // ------------------------------------------------------------

    var _REQUIRED_NON_INTERNAL_FIELDS = {
        vendor: true,
        external_process: true,
        designer: true,
    };

    function _inlineEdit(span, opts) {
        if (!span || span.dataset.editing === '1') return;
        var inputType = opts.inputType || span.dataset.inputType || 'text';
        var current = span.textContent === '—' ? '' : span.textContent;
        current = (current || '').trim();

        var control;
        if (inputType === 'textarea') {
            control = document.createElement('textarea');
            control.rows = 3;
            control.className = 'form-input';
            control.style.flex = '1';
            control.value = current;
        } else {
            control = document.createElement('input');
            control.type = (inputType === 'date') ? 'date' : 'text';
            control.className = 'form-input';
            control.style.flex = '1';
            control.value = current;
        }

        var saveBtn = document.createElement('button');
        saveBtn.type = 'button';
        saveBtn.className = 'btn sm go';
        saveBtn.innerHTML = '<i data-lucide="check" class="icon icon-sm"></i>';

        var cancelBtn = document.createElement('button');
        cancelBtn.type = 'button';
        cancelBtn.className = 'btn sm ghost';
        cancelBtn.innerHTML = '<i data-lucide="x" class="icon icon-sm"></i>';

        var errBox = document.createElement('span');
        errBox.className = 'muted';
        errBox.style.color = 'var(--err, #c0392b)';
        errBox.style.marginLeft = '8px';
        errBox.hidden = true;

        var wrap = document.createElement('span');
        wrap.style.display = 'flex';
        wrap.style.alignItems = 'center';
        wrap.style.gap = '6px';
        wrap.style.flex = '1';
        wrap.appendChild(control);
        wrap.appendChild(saveBtn);
        wrap.appendChild(cancelBtn);
        wrap.appendChild(errBox);

        // Cache so we can restore on cancel.
        var prevText = span.textContent;
        var prevHidden = [];
        // Also hide the trailing pencil button (the sibling button)
        // so the row doesn't show two action sets at once.
        var pencil = span.nextElementSibling;
        if (pencil && pencil.tagName === 'BUTTON') {
            prevHidden.push(pencil);
            pencil.style.display = 'none';
        }
        span.textContent = '';
        span.appendChild(wrap);
        span.dataset.editing = '1';
        refreshIcons(wrap);
        try { control.focus(); } catch (e) { /* ignore */ }

        function restore(text) {
            span.textContent = (text === '' || text == null) ? '—' : text;
            delete span.dataset.editing;
            prevHidden.forEach(function (el) { el.style.display = ''; });
        }

        cancelBtn.addEventListener('click', function () {
            restore(prevText);
        });

        async function commit() {
            var value = (control.value || '').trim();
            if (opts.required && !value) {
                errBox.textContent = 'Required.';
                errBox.hidden = false;
                return;
            }
            saveBtn.disabled = true;
            cancelBtn.disabled = true;
            errBox.hidden = true;
            try {
                await opts.onSave(value);
                restore(value);
                poll();
            } catch (e) {
                errBox.textContent = e.message || 'Save failed';
                errBox.hidden = false;
                saveBtn.disabled = false;
                cancelBtn.disabled = false;
            }
        }

        saveBtn.addEventListener('click', commit);
        if (inputType !== 'textarea') {
            control.addEventListener('keydown', function (ev) {
                if (ev.key === 'Enter') { ev.preventDefault(); commit(); }
                else if (ev.key === 'Escape') { ev.preventDefault(); restore(prevText); }
            });
        }
    }

    function _findFieldSpan(jobId, field) {
        return document.querySelector(
            '.job-card[data-job-id="' + jobId + '"] [data-field="' + field + '"]'
        );
    }

    W.editExternalField = function (jobId, field) {
        var span = _findFieldSpan(jobId, field);
        if (!span) {
            showToast('Field not found: ' + field, 'error');
            return;
        }
        _inlineEdit(span, {
            inputType: span.dataset.inputType || 'text',
            required: !!_REQUIRED_NON_INTERNAL_FIELDS[field],
            onSave: function (value) {
                var body = {};
                body[field] = value;
                return apiPatch('/api/jobs/' + jobId + '/external', body);
            }
        });
    };

    W.editDesignField = function (jobId, field) {
        var span = _findFieldSpan(jobId, field);
        if (!span) {
            showToast('Field not found: ' + field, 'error');
            return;
        }
        _inlineEdit(span, {
            inputType: span.dataset.inputType || 'text',
            required: !!_REQUIRED_NON_INTERNAL_FIELDS[field],
            onSave: function (value) {
                var body = {};
                body[field] = value;
                return apiPatch('/api/jobs/' + jobId + '/design', body);
            }
        });
    };

    W.startNonInternalJob = async function (jobId) {
        try {
            await apiPost('/api/jobs/' + jobId + '/start', {});
            showToast('Job #' + jobId + ' started');
            poll();
        } catch (e) {
            showToast('Error: ' + e.message, 'error');
        }
    };

    W.completeNonInternalJob = async function (jobId) {
        try {
            await apiPost('/api/jobs/' + jobId + '/complete', {});
            showToast('Job #' + jobId + ' completed');
            poll();
        } catch (e) {
            showToast('Error: ' + e.message, 'error');
        }
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
