// ============================================================
// WO Detail page (Phase 2.5c) — Flask route /work-orders/<wo_id>.
// Initial render is server-side; this module:
//   - polls /api/work-orders/<wo_id> every 2500ms
//   - manages part-selection toolbar state
//   - dispatches action handlers to existing endpoints
//   - preserves expand/collapse state across polls
//   - defers DOM rewrites while a modal is open
// All interpolated values pass through escapeHtml.
// ============================================================

(function () {
    var WO_DETAIL_POLL_MS = 2500;
    var pollTimer = null;
    var pollInflight = false;
    var woId = null;
    var selectedQueueIds = {};            // { queue_id_string: true }
    var expandedJobs = {};                // { job_id_string: true }
    var collapsedJobs = {};               // { job_id_string: true } — explicit collapses
    var lastSnapshot = null;              // latest payload

    function init() {
        var section = document.getElementById('page-wo-detail');
        if (!section) return;
        woId = section.getAttribute('data-wo-id');
        if (!woId) return;

        // Seed expanded state from the server-rendered DOM so user-driven
        // expands during the first poll round survive.
        document.querySelectorAll('.job-card').forEach(function (card) {
            var jid = card.getAttribute('data-job-id');
            if (!jid) return;
            if (card.classList.contains('job-card-expanded')) {
                expandedJobs[jid] = true;
            }
        });

        if (window.WO_DETAIL_INITIAL) {
            lastSnapshot = window.WO_DETAIL_INITIAL;
            renderRightRail(window.WO_DETAIL_INITIAL);
        }

        startPoll();
    }

    function startPoll() {
        if (pollTimer) return;
        // First tick fires after one interval — the page was just
        // server-rendered, no need to immediately re-hit the endpoint.
        pollTimer = setInterval(poll, WO_DETAIL_POLL_MS);
    }

    function stopPoll() {
        if (pollTimer) {
            clearInterval(pollTimer);
            pollTimer = null;
        }
    }

    function isModalOpen() {
        return document.querySelector('.modal-overlay.show') !== null;
    }

    async function poll() {
        if (pollInflight) return;
        if (isModalOpen()) return;  // defer — don't yank DOM out
        pollInflight = true;
        try {
            var payload = await apiGet('/api/work-orders/' + encodeURIComponent(woId));
            lastSnapshot = payload;
            renderMain(payload);
            renderRightRail(payload);
            if (typeof updateSidebarAttnBadge === 'function') {
                // Sum of failed + awaiting QC across this WO contributes
                // to the global attention; the Dashboard / Triage polls
                // own the cross-WO total. Don't override their values
                // here — just leave the badge to whichever poll touched it
                // last.
            }
        } catch (e) {
            console.error('WO Detail poll failed:', e);
        } finally {
            pollInflight = false;
        }
    }

    // ------------------------------------------------------------
    // Main column re-render (partial — preserves expand/collapse)
    // ------------------------------------------------------------

    function renderMain(wo) {
        renderCounts(wo.counts || {});
        renderPhaseTracker(wo);
        renderJobs(wo);
        renderSelectionToolbar();
    }

    function renderCounts(counts) {
        // Update the count labels + recompute the stacked progress bar.
        var labels = {
            'wo-count-done': counts.done,
            'wo-count-printing': counts.printing,
            'wo-count-queued': counts.queued,
            'wo-count-in-transit': counts.in_transit,
            'wo-count-failed': counts.failed,
        };
        Object.keys(labels).forEach(function (cls) {
            var nodes = document.querySelectorAll('.' + cls);
            nodes.forEach(function (n) {
                var word = (n.textContent || '').replace(/^\d+\s*/, '');
                n.textContent = (labels[cls] || 0) + ' ' + word.trim();
            });
        });
        var total = counts.total || 0;
        var segs = document.querySelectorAll('.wo-stack-seg');
        if (!total || segs.length < 5) return;
        // Order: ok, info, warn, busy, err (matches macro)
        var values = [counts.done, counts.printing, counts.queued,
                      counts.in_transit, counts.failed];
        segs.forEach(function (seg, i) {
            var pct = ((values[i] || 0) * 100 / total).toFixed(2);
            seg.style.width = pct + '%';
        });
    }

    function renderPhaseTracker(wo) {
        // The server-rendered tracker is structurally fine for the
        // pure-Internal case (the only case live data produces). To
        // keep this lean, only update the inspection counts inside
        // each step, not the whole structure.
        var jobs = wo.jobs || [];
        jobs.forEach(function (job) {
            var step = document.querySelector(
                '.phase-step[data-job-id="' + job.job_id + '"] .phase-step-meta'
            );
            if (!step) return;
            var inspected = (job.inspection && job.inspection.passed) || 0;
            var meta = (job.completed_parts || 0) + '/' + (job.part_count || 0);
            if (inspected) meta += ' · ' + inspected + ' inspected';
            step.textContent = meta;
        });
    }

    function renderJobs(wo) {
        // Per-job: if the job card is currently expanded, re-render its
        // body's parts groups. Header counts always update.
        (wo.jobs || []).forEach(function (job) {
            var card = document.querySelector(
                '.job-card[data-job-id="' + job.job_id + '"]'
            );
            if (!card) return;

            updateJobHeader(card, job);

            var expanded = card.classList.contains('job-card-expanded');
            if (expanded) {
                renderJobBody(card, job, wo.queue_items || []);
            }
        });
    }

    function updateJobHeader(card, job) {
        var meta = card.querySelector('.job-card-meta');
        if (meta) {
            var parts = [];
            parts.push((job.completed_parts || 0) + '/' +
                       (job.part_count || 0) + ' parts done');
            if (job.printing_parts) parts.push(job.printing_parts + ' printing');
            if (job.queued_parts) parts.push(job.queued_parts + ' queued');
            if (job.failed_parts) parts.push(job.failed_parts + ' failed');
            meta.textContent = parts.join(' · ');
        }

        // Update inspection chip
        var chip = card.querySelector('.inspection-chip');
        if (chip && job.inspection) {
            chip.outerHTML = renderInspectionChip(job);
        }
    }

    function renderInspectionChip(job) {
        var i = job.inspection || {};
        var inspector = (i.inspector || '—').toUpperCase();
        if (i.failed && i.failed > 0) {
            return '<span class="inspection-chip tone-err">INSPECTION · ' +
                i.failed + ' FAILED · ' + escapeHtml(inspector) + '</span>';
        }
        if (i.pending && i.pending > 0) {
            return '<span class="inspection-chip tone-warn">INSPECTION · ' +
                (i.passed || 0) + '/' + (i.total || 0) + ' PASSED · ' +
                i.pending + ' PENDING</span>';
        }
        if (i.total && i.total > 0 && i.passed === i.total) {
            return '<span class="inspection-chip tone-ok">ALL ' +
                i.total + ' PARTS PASSED · ' + escapeHtml(inspector) + '</span>';
        }
        return '<span class="inspection-chip tone-neutral">INSPECTION · ON COMPLETION · ' +
            escapeHtml(inspector) + '</span>';
    }

    function renderJobBody(card, job, queueItems) {
        var body = card.querySelector('.job-card-body');
        if (!body) return;

        var members = queueItems.filter(function (qi) {
            return qi.job_id === job.job_id;
        });

        // Counts cells
        var cells = body.querySelectorAll('.job-count-cell .job-count-val');
        if (cells.length >= 5) {
            cells[0].textContent = job.part_count || 0;
            cells[1].textContent = job.completed_parts || 0;
            cells[2].textContent = job.printing_parts || 0;
            cells[3].textContent = job.queued_parts || 0;
            cells[4].textContent = (job.inspection && job.inspection.pending) || 0;
        }

        // Part groups — rebuild
        var groupsHost = body.querySelector('.part-groups-host')
            || (function () {
                var existing = body.querySelectorAll('.part-group');
                var empty = body.querySelector('.part-group-empty');
                if (existing.length || empty) {
                    // Wrap existing groups so we can replace en masse.
                    var host = document.createElement('div');
                    host.className = 'part-groups-host';
                    var first = existing[0] || empty;
                    if (first && first.parentNode === body) {
                        body.insertBefore(host, first);
                        existing.forEach(function (g) { host.appendChild(g); });
                        if (empty) host.appendChild(empty);
                    } else {
                        body.appendChild(host);
                    }
                    return host;
                }
                var newHost = document.createElement('div');
                newHost.className = 'part-groups-host';
                body.appendChild(newHost);
                return newHost;
            })();

        var groupsHtml = '';
        var byStatus = {
            printing: members.filter(function (qi) { return qi.status === 'printing'; }),
            queued: members.filter(function (qi) { return qi.status === 'queued'; }),
            done: members.filter(function (qi) { return qi.status === 'completed'; }),
            failed: members.filter(function (qi) {
                return ['failed', 'upload_failed', 'start_failed', 'cancelled']
                    .indexOf(qi.status) !== -1;
            }),
        };
        if (byStatus.printing.length) {
            groupsHtml += renderPartGroup('Printing now', 'info', byStatus.printing);
        }
        if (byStatus.queued.length) {
            groupsHtml += renderPartGroup('Queued', 'warn', byStatus.queued);
        }
        if (byStatus.done.length) {
            groupsHtml += renderPartGroup('Done', 'ok', byStatus.done);
        }
        if (byStatus.failed.length) {
            groupsHtml += renderPartGroup('Failed / cancelled', 'err', byStatus.failed);
        }
        if (!groupsHtml) {
            groupsHtml = '<div class="part-group-empty">No parts assigned to this job yet.</div>';
        }
        groupsHost.innerHTML = groupsHtml;
        refreshIcons(groupsHost);

        // Restore selection checkbox state
        restoreSelectionCheckboxes();
    }

    function renderPartGroup(label, tone, parts) {
        var swatchVar = tone === 'info' ? 'info'
            : tone === 'warn' ? 'warn'
            : tone === 'ok' ? 'ok' : 'err';
        var head = '<div class="part-group-head">' +
            '<span class="part-group-swatch" style="background:var(--' + swatchVar + ');"></span>' +
            '<span class="k tone-' + swatchVar + '">' + escapeHtml(label) + '</span>' +
            '<span class="mono tab muted">' + parts.length + ' part' +
            (parts.length === 1 ? '' : 's') + '</span></div>';
        return '<div class="part-group">' + head +
            parts.map(renderPartRow).join('') + '</div>';
    }

    function renderPartRow(part) {
        var status = part.status || 'queued';
        var isPrintable = ['queued', 'cancelled', 'failed'].indexOf(status) !== -1;
        var isPrinting = status === 'printing';
        var isDone = status === 'completed';
        var qcOutcome = part.production_outcome;

        var checkbox = isPrintable
            ? '<input type="checkbox" class="wo-part-select" data-queue-id="' +
              part.queue_id + '" onchange="WoDetail.togglePartSelection(' +
              part.queue_id + ', this.checked)">'
            : '';

        var qcLine = '';
        if (isDone && qcOutcome === 'pass') {
            qcLine = '<span class="part-row-qc tone-ok">Inspection passed' +
                (part.production_operator ? ' by ' + escapeHtml(part.production_operator) : '') + '</span>';
        } else if (isDone && qcOutcome === 'fail') {
            qcLine = '<span class="part-row-qc tone-err">Inspection failed' +
                (part.production_operator ? ' by ' + escapeHtml(part.production_operator) : '') + '</span>';
        } else if (isDone) {
            qcLine = '<span class="part-row-qc tone-warn">Awaiting per-job inspection</span>';
        }

        var actions = '';
        if (isPrinting) {
            actions = '<button class="btn sm danger" onclick="WoDetail.cancelPart(' +
                part.queue_id + ", '" + escapeHtml(part.part_name) +
                "')\">Cancel</button>";
        } else if (isPrintable) {
            actions = '<button class="btn sm go" onclick="WoDetail.printPart(' +
                part.queue_id + (part.job_id ? ', ' + part.job_id : '') +
                ')"><i data-lucide="play" class="icon icon-sm"></i> Print</button>' +
                '<button class="btn sm ghost" onclick="WoDetail.cancelPart(' +
                part.queue_id + ", '" + escapeHtml(part.part_name) +
                "')\"><i data-lucide=\"x\" class=\"icon icon-sm\"></i></button>";
        } else if (isDone && qcOutcome !== 'pass' && qcOutcome !== 'fail') {
            actions = '<button class="btn sm primary" onclick="WoDetail.setQC(' +
                (part.print_job_id || 'null') + ', ' + part.queue_id +
                ')">Inspect <i data-lucide="chevron-right" class="icon icon-sm"></i></button>';
        } else if (status === 'upload_failed' || status === 'start_failed') {
            actions = '<button class="btn sm warn" onclick="WoDetail.retryPart(' +
                part.queue_id + ')">Retry ' +
                (status === 'start_failed' ? 'Start' : 'Upload') + '</button>';
        }

        return '<div class="part-row" data-queue-id="' + part.queue_id +
            '" data-status="' + escapeHtml(status) +
            '" data-printable="' + (isPrintable ? '1' : '0') + '">' +
            '<span class="part-row-checkbox">' + checkbox + '</span>' +
            '<div class="part-row-main">' +
                '<div class="part-row-title">' +
                    '<span class="part-row-name">' + escapeHtml(part.part_name) + '</span>' +
                    '<span class="mono tab muted part-row-seq">' +
                    (part.sequence_number || '?') + '/' + (part.total_quantity || '?') + '</span>' +
                    '<span class="mono muted part-row-qid">Q-' + part.queue_id + '</span>' +
                '</div>' +
                '<div class="part-row-meta">' +
                    '<span>' + escapeHtml(part.material || '') + '</span>' +
                    (part.assigned_printer_name
                        ? '<span class="muted">&middot; ' + escapeHtml(part.assigned_printer_name) + '</span>'
                        : '') +
                    qcLine +
                '</div>' +
            '</div>' +
            '<div class="part-row-actions">' + actions + '</div>' +
        '</div>';
    }

    function restoreSelectionCheckboxes() {
        document.querySelectorAll('.wo-part-select').forEach(function (input) {
            var qid = input.getAttribute('data-queue-id');
            input.checked = !!selectedQueueIds[qid];
        });
    }

    // ------------------------------------------------------------
    // Right rail — Needs You + Activity
    // ------------------------------------------------------------

    function renderRightRail(wo) {
        renderNeedsYou(wo);
        renderActivity(wo);
    }

    function renderNeedsYou(wo) {
        var host = document.getElementById('wo-needs-you-body');
        if (!host) return;
        var queue = wo.queue_items || [];
        var awaitingQc = queue.filter(function (qi) {
            return qi.status === 'completed' &&
                (!qi.production_outcome || qi.production_outcome === 'unknown');
        });
        var failed = queue.filter(function (qi) {
            return ['failed', 'upload_failed', 'start_failed'].indexOf(qi.status) !== -1;
        });

        if (!awaitingQc.length && !failed.length) {
            host.innerHTML = '<div class="muted needs-you-empty">All caught up</div>';
            return;
        }

        var blocks = [];
        if (awaitingQc.length) {
            blocks.push(
                '<div class="needs-you-item tone-warn">' +
                '<div class="needs-you-item-title">Inspect ' +
                awaitingQc.length + ' part' +
                (awaitingQc.length === 1 ? '' : 's') + '</div>' +
                '<div class="muted needs-you-item-sub">' +
                escapeHtml(awaitingQc.slice(0, 3).map(function (qi) {
                    return qi.part_name + ' ' + qi.sequence_number + '/' + qi.total_quantity;
                }).join(' · ')) +
                (awaitingQc.length > 3 ? ' …' : '') +
                '</div>' +
                '<button class="btn sm primary" onclick="WoDetail.setQC(' +
                (awaitingQc[0].print_job_id || 'null') + ', ' +
                awaitingQc[0].queue_id + ')">Open Inspection <i data-lucide="chevron-right" class="icon icon-sm"></i></button>' +
                '</div>'
            );
        }
        if (failed.length) {
            blocks.push(
                '<div class="needs-you-item tone-err">' +
                '<div class="needs-you-item-title">' +
                failed.length + ' failed part' +
                (failed.length === 1 ? '' : 's') + '</div>' +
                '<div class="muted needs-you-item-sub">' +
                escapeHtml(failed.slice(0, 3).map(function (qi) {
                    return qi.part_name + ' ' + qi.sequence_number + '/' + qi.total_quantity;
                }).join(' · ')) +
                (failed.length > 3 ? ' …' : '') +
                '</div>' +
                '</div>'
            );
        }
        host.innerHTML = blocks.join('');
        refreshIcons(host);
    }

    function renderActivity(wo) {
        var host = document.getElementById('wo-activity-body');
        if (!host) return;
        var events = wo.activity || [];
        if (!events.length) {
            host.innerHTML = '<div class="muted activity-empty">No activity yet</div>';
            return;
        }
        host.innerHTML = events.map(function (e) {
            return '<div class="activity-event">' +
                '<span class="mono muted activity-event-ts">' +
                escapeHtml(formatActivityTs(e.ts)) + '</span>' +
                '<span class="activity-event-dot" style="background:var(--' +
                escapeHtml(e.tone || 'neutral') + ');"></span>' +
                '<span class="activity-event-text">' + escapeHtml(e.text || '') + '</span>' +
                (e.where ? '<span class="muted activity-event-where">' +
                    escapeHtml(e.where) + '</span>' : '') +
                '</div>';
        }).join('');
    }

    function formatActivityTs(iso) {
        if (!iso) return '';
        try {
            var d = new Date(iso);
            if (isNaN(d.getTime())) return iso.slice(0, 16);
            return d.toLocaleTimeString([], { hour: '2-digit', minute: '2-digit' });
        } catch (e) {
            return iso.slice(0, 16);
        }
    }

    // ------------------------------------------------------------
    // Selection toolbar
    // ------------------------------------------------------------

    function renderSelectionToolbar() {
        var bar = document.getElementById('wo-selection-toolbar');
        var countEl = document.getElementById('wo-selection-count');
        var createBtn = document.getElementById('wo-create-job-btn');
        var printBtn = document.getElementById('wo-print-selected-btn');
        if (!bar || !countEl) return;

        var n = Object.keys(selectedQueueIds).length;
        if (n === 0) {
            bar.style.display = 'none';
            if (createBtn) createBtn.disabled = true;
            return;
        }
        bar.style.display = '';
        countEl.textContent = n + ' part' + (n === 1 ? '' : 's') + ' selected';
        if (createBtn) createBtn.disabled = false;
        if (printBtn) printBtn.disabled = false;
    }

    function togglePartSelection(queueId, checked) {
        var key = String(queueId);
        if (checked) selectedQueueIds[key] = true;
        else delete selectedQueueIds[key];
        renderSelectionToolbar();
    }

    function clearSelection() {
        selectedQueueIds = {};
        document.querySelectorAll('.wo-part-select').forEach(function (input) {
            input.checked = false;
        });
        renderSelectionToolbar();
    }

    function getSelectedQueueIds() {
        return Object.keys(selectedQueueIds).map(function (k) { return parseInt(k, 10); })
            .filter(function (n) { return !isNaN(n); });
    }

    // ------------------------------------------------------------
    // Expand/collapse
    // ------------------------------------------------------------

    function toggleJob(jobId) {
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
            expandedJobs[jobId] = true;
            delete collapsedJobs[jobId];
            // Render the body now in case it was server-rendered collapsed.
            if (lastSnapshot) {
                var job = (lastSnapshot.jobs || []).find(function (j) {
                    return String(j.job_id) === String(jobId);
                });
                if (job) renderJobBody(card, job, lastSnapshot.queue_items || []);
            }
        } else {
            delete expandedJobs[jobId];
            collapsedJobs[jobId] = true;
        }
    }

    // ------------------------------------------------------------
    // Action handlers
    // ------------------------------------------------------------

    function openCancelConfirm(title, body, onConfirm) {
        var titleEl = document.getElementById('cancelConfirmTitle');
        var bodyEl = document.getElementById('cancelConfirmBody');
        var btn = document.getElementById('cancelConfirmBtn');
        if (!titleEl || !bodyEl || !btn) {
            // Fallback to native confirm.
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

    async function cancelWO(woIdArg) {
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
    }

    async function retryWO(woIdArg) {
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
    }

    async function cancelJob(woIdArg, jobId) {
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
    }

    async function retryJob(woIdArg, jobId) {
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
    }

    async function cancelPart(queueId, partName) {
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
    }

    function printPart(queueId, jobId) {
        // Reuse the existing Print modal; pre-stuff its expected
        // _woDetailQueueItems global from our snapshot so it can show
        // the part metadata without hitting a (now-gone) /api/queue.
        var snapshot = lastSnapshot || window.WO_DETAIL_INITIAL;
        if (snapshot && typeof window._woDetailQueueItems !== 'undefined') {
            window._woDetailQueueItems = snapshot.queue_items || [];
            window._woDetailId = snapshot.wo_id;
        }
        if (typeof showQueuePrintModal !== 'function') {
            showToast('Print modal unavailable', 'error');
            return;
        }
        showQueuePrintModal(queueId, jobId || '');
    }

    function printJob(jobId) {
        var snapshot = lastSnapshot || window.WO_DETAIL_INITIAL;
        if (snapshot && typeof window._woDetailQueueItems !== 'undefined') {
            window._woDetailQueueItems = snapshot.queue_items || [];
            window._woDetailId = snapshot.wo_id;
        }
        showQueuePrintModal([], jobId);
    }

    function printSelected() {
        var queueIds = getSelectedQueueIds();
        if (!queueIds.length) {
            showToast('Select at least one part to print', 'error');
            return;
        }
        // Validate they all share one job (or are unassigned).
        var snapshot = lastSnapshot || window.WO_DETAIL_INITIAL;
        var queueItems = (snapshot && snapshot.queue_items) || [];
        var jobIds = {};
        queueItems.filter(function (qi) {
            return selectedQueueIds[String(qi.queue_id)];
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
    }

    async function groupIntoNewJob(woIdArg) {
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
                clearSelection();
                poll();
            } else {
                showToast('Error: Unknown response', 'error');
            }
        } catch (e) {
            showToast('Error: ' + e.message, 'error');
        }
    }

    async function createJobFromSelected(woIdArg) {
        return groupIntoNewJob(woIdArg);
    }

    function setQC(printJobId, queueId) {
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

        // Pre-fill from cached snapshot if we have the QI.
        var snapshot = lastSnapshot || window.WO_DETAIL_INITIAL;
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
    }

    async function retryPart(queueId) {
        try {
            var result = await apiPost('/api/queue/' + queueId + '/retry', {});
            showToast(result.message || 'Retry sent to printer');
            poll();
        } catch (e) {
            showToast('Error: ' + e.message, 'error');
        }
    }

    // ------------------------------------------------------------
    // Expose
    // ------------------------------------------------------------

    window.WoDetail = {
        start: function (id) { woId = id; init(); startPoll(); },
        stop: stopPoll,
        poll: poll,
        toggleJob: toggleJob,
        togglePartSelection: togglePartSelection,
        clearSelection: clearSelection,
        printPart: printPart,
        printJob: printJob,
        printSelected: printSelected,
        groupIntoNewJob: groupIntoNewJob,
        createJobFromSelected: createJobFromSelected,
        cancelPart: cancelPart,
        cancelJob: cancelJob,
        retryJob: retryJob,
        cancelWO: cancelWO,
        retryWO: retryWO,
        setQC: setQC,
        retryPart: retryPart,
    };

    // ------------------------------------------------------------
    // submitWoQc — preserved from legacy detail.js (QC modal flow).
    // Lives globally because the modal markup calls it via inline
    // onclick from the modal partial.
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
                if (window.WoDetail && typeof window.WoDetail.poll === 'function') {
                    window.WoDetail.poll();
                }
            } catch (e) {
                showToast('Error: ' + e.message, 'error');
            }
        };
    }

    // Boot when the page is ready.
    if (document.readyState === 'loading') {
        document.addEventListener('DOMContentLoaded', init);
    } else {
        init();
    }
})();
