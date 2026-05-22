// ============================================================
// WO Detail page — render module (Phase 5f split).
// Pure HTML-string builders + DOM mutators. No fetch calls,
// no event handlers. State lives in window.WoDetail._state;
// this module reads from it but does not own it.
// Exposed entry points hang off window.WoDetail._render.
// All interpolated values pass through escapeHtml (from core/dom.js).
// ============================================================

(function () {
    var W = (window.WoDetail = window.WoDetail || {});
    // Shared mutable state — initialised by whichever module loads first;
    // the same shape is repeated in detail-actions.js and index.js so
    // each file is resilient to load-order changes.
    W._state = W._state || {
        pollTimer: null,
        pollInflight: false,
        woId: null,
        selectedQueueIds: {},
        expandedJobs: {},
        collapsedJobs: {},
        lastSnapshot: null,
    };
    W._render = W._render || {};
    var S = W._state;
    var R = W._render;

    // ------------------------------------------------------------
    // Main column
    // ------------------------------------------------------------

    function renderMain(wo) {
        renderCounts(wo.counts || {});
        renderPhaseTracker(wo);
        renderJobs(wo);
        renderSelectionToolbar();
    }

    function renderCounts(counts) {
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
        var values = [counts.done, counts.printing, counts.queued,
                      counts.in_transit, counts.failed];
        segs.forEach(function (seg, i) {
            var pct = ((values[i] || 0) * 100 / total).toFixed(2);
            seg.style.width = pct + '%';
        });
    }

    function renderPhaseTracker(wo) {
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

        var cells = body.querySelectorAll('.job-count-cell .job-count-val');
        if (cells.length >= 5) {
            cells[0].textContent = job.part_count || 0;
            cells[1].textContent = job.completed_parts || 0;
            cells[2].textContent = job.printing_parts || 0;
            cells[3].textContent = job.queued_parts || 0;
            cells[4].textContent = (job.inspection && job.inspection.pending) || 0;
        }

        var groupsHost = body.querySelector('.part-groups-host')
            || (function () {
                var existing = body.querySelectorAll('.part-group');
                var empty = body.querySelector('.part-group-empty');
                if (existing.length || empty) {
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
            input.checked = !!S.selectedQueueIds[qid];
        });
    }

    // ------------------------------------------------------------
    // Right rail
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

        var n = Object.keys(S.selectedQueueIds).length;
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

    // ------------------------------------------------------------
    // Public render entry points used by orchestration + actions.
    // ------------------------------------------------------------

    R.main = renderMain;
    R.rightRail = renderRightRail;
    R.selectionToolbar = renderSelectionToolbar;
    R.jobBody = renderJobBody;
})();
