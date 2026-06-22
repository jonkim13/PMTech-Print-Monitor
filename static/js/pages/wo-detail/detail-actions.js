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

    // ------------------------------------------------------------
    // Batch 4 — Add Internal Job modal. Mirrors the non-Internal
    // modal structure but collects a Parts sub-list (add/remove part
    // rows, ≥1) and POSTs {job_type:'Internal', parts:[...]} to
    // /api/workorders/<wo_id>/jobs. The wo_id is stashed on the
    // modal's dataset (same pattern as the non-Internal modal).
    // ------------------------------------------------------------

    function _cijSetError(message) {
        var box = document.getElementById('cij-error');
        if (!box) return;
        if (message) {
            box.textContent = message;
            box.hidden = false;
        } else {
            box.textContent = '';
            box.hidden = true;
        }
    }

    function _cijPartRowHtml() {
        return '' +
            '<div class="cij-part-row">' +
            '<div class="form-group" style="flex:2 1 200px;">' +
            '<label class="form-label">Part Name</label>' +
            '<input type="text" class="form-input cij-part-name" placeholder="e.g. Widget Bracket">' +
            '</div>' +
            '<div class="form-group" style="flex:1 1 140px;">' +
            '<label class="form-label">Material</label>' +
            '<select class="form-input cij-material"><option value="">Select...</option></select>' +
            '</div>' +
            '<div class="form-group" style="flex:0 0 auto;">' +
            '<label class="form-label">Qty</label>' +
            '<div class="wo-qty-stepper">' +
            '<button type="button" class="wo-qty-btn wo-qty-btn-minus" onclick="WoDetail.cijQtyStep(this, -1)" aria-label="Decrease quantity">−</button>' +
            '<input type="number" class="wo-qty-value cij-quantity" min="1" value="1" readonly>' +
            '<button type="button" class="wo-qty-btn wo-qty-btn-plus" onclick="WoDetail.cijQtyStep(this, 1)" aria-label="Increase quantity">+</button>' +
            '</div>' +
            '</div>' +
            '<button type="button" class="btn sm danger cij-part-remove" onclick="WoDetail.cijRemovePartRow(this)" aria-label="Remove part"><i data-lucide="x" class="icon icon-sm"></i></button>' +
            '</div>';
    }

    async function _cijLoadMaterials(selectEl) {
        if (!selectEl) return;
        try {
            var options = await apiGet('/api/inventory/options');
            var materials = options.materials || [];
            selectEl.innerHTML = '<option value="">Select...</option>' +
                materials.map(function (m) {
                    return '<option value="' + escapeHtml(m) + '">' +
                        escapeHtml(m) + '</option>';
                }).join('');
        } catch (e) {
            selectEl.innerHTML = '<option value="">Error</option>';
        }
    }

    W.cijQtyStep = function (btn, delta) {
        var input = btn.parentNode.querySelector('.cij-quantity');
        if (!input) return;
        var v = (parseInt(input.value, 10) || 1) + delta;
        if (v < 1) v = 1;
        input.value = v;
    };

    W.cijAddPartRow = function () {
        var rows = document.getElementById('cij-part-rows');
        if (!rows) return;
        var tmp = document.createElement('div');
        tmp.innerHTML = _cijPartRowHtml();
        var row = tmp.firstChild;
        rows.appendChild(row);
        refreshIcons(row);
        _cijLoadMaterials(row.querySelector('.cij-material'));
    };

    W.cijRemovePartRow = function (btn) {
        var rows = document.getElementById('cij-part-rows');
        var row = btn.closest('.cij-part-row');
        if (!rows || !row) return;
        if (rows.querySelectorAll('.cij-part-row').length <= 1) return;
        row.remove();
    };

    W.openInternalJobModal = function (woIdArg) {
        var modal = document.getElementById('create-internal-job-modal');
        if (!modal) {
            showToast('Add-Internal-Job modal unavailable', 'error');
            return;
        }
        modal.dataset.woId = woIdArg || '';
        var rows = document.getElementById('cij-part-rows');
        if (rows) rows.innerHTML = '';
        _cijSetError('');
        W.cijAddPartRow();  // start with one part row
        showModal('create-internal-job-modal');
    };

    W.submitInternalJob = async function () {
        var modal = document.getElementById('create-internal-job-modal');
        if (!modal) return;
        var woIdArg = modal.dataset.woId || '';
        if (!woIdArg) {
            _cijSetError('Missing work order id.');
            return;
        }

        var parts = [];
        var bad = false;
        document.querySelectorAll('#cij-part-rows .cij-part-row')
            .forEach(function (row) {
                if (bad) return;
                var partName = (row.querySelector('.cij-part-name').value || '').trim();
                var material = row.querySelector('.cij-material').value;
                var quantity = parseInt(row.querySelector('.cij-quantity').value, 10) || 0;
                if (!partName || !material || quantity < 1) {
                    bad = true;
                    return;
                }
                parts.push({ part_name: partName, material: material, quantity: quantity });
            });
        if (bad) {
            _cijSetError('Fill in Part Name, Material, and Qty for every part.');
            return;
        }
        if (parts.length === 0) {
            _cijSetError('Add at least one part.');
            return;
        }

        _cijSetError('');
        try {
            var result = await apiPost(
                '/api/workorders/' + encodeURIComponent(woIdArg) + '/jobs',
                { job_type: 'Internal', parts: parts }
            );
            showToast('Internal job #' + (result.job_id || '?') + ' added — ' +
                (result.parts_created || parts.length) + ' part' +
                ((result.parts_created || parts.length) === 1 ? '' : 's') + ' queued');
            hideModal('create-internal-job-modal');
            poll();
        } catch (e) {
            _cijSetError(e.message || 'Request failed');
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

    // ------------------------------------------------------------
    // Phase D — job-level inspection gate. Internal + External jobs
    // route through inspection before completion is recognized. The
    // modal records pass/fail via POST /api/jobs/<id>/inspection,
    // which writes the outcome and re-rolls job + WO status. The job
    // id + type are stashed on the dialog dataset (mirrors woQcModal).
    // ------------------------------------------------------------

    function _inspectionSetError(message) {
        var box = document.getElementById('inspectionError');
        if (!box) return;
        if (message) {
            box.textContent = message;
            box.hidden = false;
        } else {
            box.textContent = '';
            box.hidden = true;
        }
    }

    W.openInspectionModal = function (jobId, jobType) {
        var modal = document.getElementById('inspectionModal');
        if (!modal) {
            showToast('Inspection modal unavailable', 'error');
            return;
        }
        modal.dataset.jobId = jobId;
        modal.dataset.jobType = jobType || '';

        var titleEl = document.getElementById('inspectionTitle');
        if (titleEl) {
            titleEl.textContent = 'Inspect Job #' + jobId;
        }
        var outcomeEl = document.getElementById('inspectionOutcome');
        var inspectorEl = document.getElementById('inspectionInspector');
        var reportEl = document.getElementById('inspectionReport');
        var dateEl = document.getElementById('inspectionDate');
        if (outcomeEl) outcomeEl.value = 'pass';
        if (inspectorEl) inspectorEl.value = '';
        if (reportEl) reportEl.value = '';
        if (dateEl) dateEl.value = new Date().toISOString().slice(0, 10);
        _inspectionSetError('');
        showModal('inspectionModal');
    };

    W.submitInspection = async function () {
        var modal = document.getElementById('inspectionModal');
        if (!modal) return;
        var jobId = parseInt(modal.dataset.jobId, 10);
        if (!jobId) {
            _inspectionSetError('No job selected.');
            return;
        }
        var outcome = (document.getElementById('inspectionOutcome') || {}).value || 'pass';
        var inspector = ((document.getElementById('inspectionInspector') || {}).value || '').trim();
        var report = ((document.getElementById('inspectionReport') || {}).value || '').trim();
        var date = (document.getElementById('inspectionDate') || {}).value || '';
        if (!inspector) {
            _inspectionSetError('Inspector name is required.');
            return;
        }
        _inspectionSetError('');
        var body = { outcome: outcome, inspector: inspector };
        if (report) body.report = report;
        if (date) body.date = date;
        try {
            await apiPost('/api/jobs/' + jobId + '/inspection', body);
            showToast('Inspection ' + (outcome === 'pass' ? 'passed' : 'failed') +
                ' · Job #' + jobId);
            hideModal('inspectionModal');
            poll();
        } catch (e) {
            _inspectionSetError(e.message || 'Request failed');
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

    // ------------------------------------------------------------
    // Phase E2 — Non-Conformance (NCR) + Corrective Action (CA).
    // Entry point is the "Create NCR" button on a failed-inspection
    // card (openNcrModal). The NCR detail modal (openNcrDetail) is
    // JS-rendered from GET /api/ncrs/<id> and hosts the CA add/verify
    // and Close-NCR actions. Modals stash their target id on the
    // dialog dataset (mirrors the inspection-modal pattern). After a
    // gate-affecting mutation (create NCR, close NCR) we poll() so the
    // WO header + NCR section reflect the open-NCR rollup gate; CA
    // add/verify only re-render the (still-open) detail modal.
    // ------------------------------------------------------------

    function _val(id) {
        var el = document.getElementById(id);
        return (el && el.value) || '';
    }

    function _setBoxError(boxId, message) {
        var box = document.getElementById(boxId);
        if (!box) return;
        if (message) {
            box.textContent = message;
            box.hidden = false;
        } else {
            box.textContent = '';
            box.hidden = true;
        }
    }

    W.openNcrModal = function (jobId, woId) {
        var modal = document.getElementById('createNcrModal');
        if (!modal) {
            showToast('NCR modal unavailable', 'error');
            return;
        }
        modal.dataset.jobId = jobId;
        modal.dataset.woId = woId || '';
        var title = document.getElementById('createNcrTitle');
        if (title) title.textContent = 'Raise NCR · Job #' + jobId;
        ['ncrDescription', 'ncrReportedBy', 'ncrAffectedParts',
         'ncrRemedialAction'].forEach(function (id) {
            var el = document.getElementById(id);
            if (el) el.value = '';
        });
        var nRadio = document.querySelector(
            'input[name="ncrCaNeeded"][value="N"]'
        );
        if (nRadio) nRadio.checked = true;
        _setBoxError('createNcrError', '');
        showModal('createNcrModal');
    };

    W.submitNcr = async function () {
        var modal = document.getElementById('createNcrModal');
        if (!modal) return;
        var jobId = parseInt(modal.dataset.jobId, 10);
        var woId = modal.dataset.woId || '';
        var description = _val('ncrDescription').trim();
        var reportedBy = _val('ncrReportedBy').trim();
        var affectedParts = _val('ncrAffectedParts').trim();
        var remedial = _val('ncrRemedialAction').trim();
        var caNeeded = (document.querySelector(
            'input[name="ncrCaNeeded"]:checked'
        ) || {}).value || 'N';
        if (!description) {
            _setBoxError('createNcrError', 'Description is required.');
            return;
        }
        if (!reportedBy) {
            _setBoxError('createNcrError', 'Reported by is required.');
            return;
        }
        if (!jobId || !woId) {
            _setBoxError('createNcrError',
                'Missing job / work-order context.');
            return;
        }
        _setBoxError('createNcrError', '');
        var body = {
            job_id: jobId, wo_id: woId, description: description,
            reported_by: reportedBy, corrective_action_needed: caNeeded,
        };
        if (affectedParts) body.affected_parts = affectedParts;
        if (remedial) body.remedial_action = remedial;
        try {
            var result = await apiPost('/api/ncrs', body);
            var ncrId = result && result.ncr && result.ncr.ncr_id;
            showToast('NCR #' + (ncrId || '?') + ' raised');
            hideModal('createNcrModal');
            poll();  // open NCR gates the WO → header goes attention
        } catch (e) {
            _setBoxError('createNcrError', e.message || 'Request failed');
        }
    };

    W.openNcrDetail = async function (ncrId) {
        var modal = document.getElementById('ncrDetailModal');
        if (!modal) {
            showToast('NCR detail unavailable', 'error');
            return;
        }
        modal.dataset.ncrId = ncrId;
        _setBoxError('ncrDetailError', '');
        var body = document.getElementById('ncrDetailBody');
        if (body) body.innerHTML = '<div class="muted">Loading…</div>';
        showModal('ncrDetailModal');
        await _renderNcrDetail(ncrId);
    };

    async function _renderNcrDetail(ncrId) {
        var body = document.getElementById('ncrDetailBody');
        try {
            var result = await apiGet('/api/ncrs/' + ncrId);
            var ncr = result.ncr || {};
            var title = document.getElementById('ncrDetailTitle');
            if (title) {
                title.textContent = 'NCR #' + ncr.ncr_id + ' · ' +
                    String(ncr.status || '').toUpperCase();
            }
            if (body) {
                body.innerHTML = _ncrDetailHtml(ncr);
                refreshIcons(body);
            }
        } catch (e) {
            if (body) {
                body.innerHTML = '<div class="muted" style="color:var(--err)">' +
                    escapeHtml(e.message || 'Failed to load NCR') + '</div>';
            }
        }
    }

    function _ncrField(label, value) {
        return '<div class="job-field-row" style="display:flex; gap:10px; padding:5px 0;">' +
            '<span class="k" style="min-width:150px;">' + escapeHtml(label) + '</span>' +
            '<span class="mono" style="flex:1; word-break:break-word;">' +
            escapeHtml(value || '—') + '</span></div>';
    }

    var _CA_PILL_KINDS = {
        open: 'queued', in_progress: 'printing',
        verified: 'done', closed: 'idle',
    };

    function _caStatusPill(status) {
        var s = status || 'open';
        var kind = _CA_PILL_KINDS[s] || 'queued';
        return '<span class="st st-' + kind + '"><i class="sym sym-' + kind +
            '"></i><span>' +
            escapeHtml(s.toUpperCase().replace(/_/g, ' ')) + '</span></span>';
    }

    function _caHtml(ca) {
        var verifyBtn = '';
        if (ca.status === 'open' || ca.status === 'in_progress') {
            verifyBtn = '<button class="btn sm primary" onclick="WoDetail.openCaVerifyModal(' +
                ca.ca_id + ')"><i data-lucide="check-check" class="icon icon-sm"></i> Verify</button>';
        }
        return '<div class="card card-pad" style="margin:8px 0;">' +
            '<div style="display:flex; align-items:center; gap:8px; margin-bottom:6px;">' +
            '<span class="mono muted">CA #' + escapeHtml(String(ca.ca_id)) + '</span>' +
            _caStatusPill(ca.status) +
            '<div style="flex:1;"></div>' + verifyBtn + '</div>' +
            _ncrField('Root cause actions', ca.root_cause_actions) +
            _ncrField('Responsible', ca.responsible_persons) +
            _ncrField('Resources', ca.resources_needed) +
            _ncrField('Effectiveness', ca.effectiveness_verification) +
            _ncrField('Verifying person', ca.verifying_person) +
            '</div>';
    }

    function _ncrDetailHtml(ncr) {
        var cas = ncr.corrective_actions || [];
        var html = '';
        html += _ncrField('Description', ncr.description);
        html += _ncrField('Reported by', ncr.reported_by);
        html += _ncrField('Affected parts', ncr.affected_parts);
        html += _ncrField('Remedial action', ncr.remedial_action);
        html += _ncrField('CA needed', ncr.corrective_action_needed);
        html += _ncrField('Status', ncr.status);
        html += _ncrField('Created', String(ncr.created_at || '').slice(0, 10));
        if (ncr.closed_at) {
            html += _ncrField('Closed', String(ncr.closed_at).slice(0, 10));
        }

        html += '<div style="display:flex; align-items:center; margin:14px 0 4px;">' +
            '<div class="k">Corrective actions (' + cas.length + ')</div>' +
            '<div style="flex:1;"></div>';
        if (ncr.corrective_action_needed === 'Y' && cas.length === 0) {
            html += '<button class="btn sm go" onclick="WoDetail.openCaModal(' +
                ncr.ncr_id + ')"><i data-lucide="plus" class="icon icon-sm"></i> Add Corrective Action</button>';
        }
        html += '</div>';

        if (!cas.length) {
            html += (ncr.corrective_action_needed === 'Y')
                ? '<div class="muted">No corrective action yet — one is required before this NCR can close.</div>'
                : '<div class="muted">No corrective action required for this NCR.</div>';
        } else {
            html += cas.map(_caHtml).join('');
        }

        if (ncr.status === 'open') {
            html += '<div style="margin-top:14px; display:flex; gap:8px;">' +
                '<button class="btn sm danger" onclick="WoDetail.closeNcr(' +
                ncr.ncr_id + ')"><i data-lucide="check" class="icon icon-sm"></i> Close NCR</button>' +
                '</div>';
        }
        return html;
    }

    W.openCaModal = function (ncrId) {
        var modal = document.getElementById('createCaModal');
        if (!modal) {
            showToast('Corrective-action modal unavailable', 'error');
            return;
        }
        modal.dataset.ncrId = ncrId;
        ['caRootCause', 'caResponsible', 'caResources',
         'caEffectiveness', 'caVerifyingPerson'].forEach(function (id) {
            var el = document.getElementById(id);
            if (el) el.value = '';
        });
        _setBoxError('createCaError', '');
        showModal('createCaModal');
    };

    W.submitCa = async function () {
        var modal = document.getElementById('createCaModal');
        if (!modal) return;
        var ncrId = parseInt(modal.dataset.ncrId, 10);
        var root = _val('caRootCause').trim();
        if (!ncrId) {
            _setBoxError('createCaError', 'Missing NCR context.');
            return;
        }
        if (!root) {
            _setBoxError('createCaError', 'Root cause actions are required.');
            return;
        }
        var body = { root_cause_actions: root };
        var resp = _val('caResponsible').trim();
        if (resp) body.responsible_persons = resp;
        var res = _val('caResources').trim();
        if (res) body.resources_needed = res;
        var eff = _val('caEffectiveness').trim();
        if (eff) body.effectiveness_verification = eff;
        var vp = _val('caVerifyingPerson').trim();
        if (vp) body.verifying_person = vp;
        _setBoxError('createCaError', '');
        try {
            await apiPost('/api/ncrs/' + ncrId + '/corrective-actions', body);
            showToast('Corrective action added');
            hideModal('createCaModal');
            // Detail modal is still open behind this one — refresh it.
            await _renderNcrDetail(ncrId);
        } catch (e) {
            _setBoxError('createCaError', e.message || 'Request failed');
        }
    };

    W.openCaVerifyModal = function (caId) {
        var modal = document.getElementById('verifyCaModal');
        if (!modal) {
            showToast('Verify modal unavailable', 'error');
            return;
        }
        modal.dataset.caId = caId;
        var el = document.getElementById('verifyCaPerson');
        if (el) el.value = '';
        _setBoxError('verifyCaError', '');
        showModal('verifyCaModal');
    };

    W.submitCaVerify = async function () {
        var modal = document.getElementById('verifyCaModal');
        if (!modal) return;
        var caId = parseInt(modal.dataset.caId, 10);
        var person = _val('verifyCaPerson').trim();
        if (!caId) {
            _setBoxError('verifyCaError',
                'Missing corrective-action context.');
            return;
        }
        if (!person) {
            _setBoxError('verifyCaError', 'Verifying person is required.');
            return;
        }
        _setBoxError('verifyCaError', '');
        try {
            await apiPost('/api/corrective-actions/' + caId + '/verify',
                { verifying_person: person });
            showToast('Corrective action verified');
            hideModal('verifyCaModal');
            var detailModal = document.getElementById('ncrDetailModal');
            var ncrId = detailModal &&
                parseInt(detailModal.dataset.ncrId, 10);
            if (ncrId) await _renderNcrDetail(ncrId);
        } catch (e) {
            _setBoxError('verifyCaError', e.message || 'Request failed');
        }
    };

    W.closeNcr = async function (ncrId) {
        try {
            await apiPost('/api/ncrs/' + ncrId + '/close', {});
            showToast('NCR #' + ncrId + ' closed');
            hideModal('ncrDetailModal');
            poll();  // closing the last open NCR may release the WO gate
        } catch (e) {
            // 409 (unverified CA) etc — surface inline, keep the modal
            // open so the operator sees why the close was rejected.
            _setBoxError('ncrDetailError', e.message || 'Could not close NCR');
        }
    };

    // ------------------------------------------------------------
    // Phase F — Delivery. The "Mark Delivered" WO-level action shows
    // only when the WO is 'completed'. The modal records the delivery
    // and stamps the WO 'delivered' (a manual terminal status that
    // survives subsequent re-derivation). On success poll() re-reads
    // the WO so the header pill flips to DELIVERED and the button
    // becomes the inline delivery stamp.
    // ------------------------------------------------------------

    W.openDeliverModal = function (woId) {
        var modal = document.getElementById('deliverWoModal');
        if (!modal) {
            showToast('Deliver modal unavailable', 'error');
            return;
        }
        modal.dataset.woId = woId || '';
        var dateEl = document.getElementById('deliverDate');
        if (dateEl) dateEl.value = new Date().toISOString().slice(0, 10);
        ['deliverReceivedBy', 'deliverRecordedBy', 'deliverNotes']
            .forEach(function (id) {
                var el = document.getElementById(id);
                if (el) el.value = '';
            });
        _setBoxError('deliverError', '');
        showModal('deliverWoModal');
    };

    W.submitDeliver = async function () {
        var modal = document.getElementById('deliverWoModal');
        if (!modal) return;
        var woId = modal.dataset.woId || '';
        if (!woId) {
            _setBoxError('deliverError', 'Missing work order id.');
            return;
        }
        var body = {};
        var deliveredAt = _val('deliverDate');
        if (deliveredAt) body.delivered_at = deliveredAt;
        var receivedBy = _val('deliverReceivedBy').trim();
        if (receivedBy) body.received_by = receivedBy;
        var recordedBy = _val('deliverRecordedBy').trim();
        if (recordedBy) body.recorded_by = recordedBy;
        var notes = _val('deliverNotes').trim();
        if (notes) body.notes = notes;
        _setBoxError('deliverError', '');
        try {
            await apiPost(
                '/api/workorders/' + encodeURIComponent(woId) + '/deliver',
                body
            );
            showToast('Marked delivered · ' + woId);
            hideModal('deliverWoModal');
            poll();  // WO flips to delivered; button → delivery stamp
        } catch (e) {
            _setBoxError('deliverError', e.message || 'Request failed');
        }
    };
})();
