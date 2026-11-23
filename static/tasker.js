/* Tasker board — isolated tracking page.
 *
 * Talks ONLY to /tasker/api/*.  No other AMS module is read or written.
 * Alarm: looping Web Audio beep while any deadline is overdue; it stops only
 * when an admin resets the missed deadline (or the board reports none).
 */
(function () {
    'use strict';
    window.TASKER_PAGE_ACTIVE = true;

    var board = { counters: {}, tasks: [], is_admin: false };
    var filter = 'all';
    var searchQ = '';
    var expanded = {};
    var selected = {};          // task id -> true (ticked rows, admin cleanup)
    var pollTimer = null;
    var tickTimer = null;
    var cleanNoticeUntil = 0;
    var cleanNoticeText = '';

    // ------------------------------------------------------------------ dom
    function $(id) { return document.getElementById(id); }
    function findTask(id) {
        for (var i = 0; i < board.tasks.length; i++) {
            if (String(board.tasks[i].id) === String(id)) return board.tasks[i];
        }
        return null;
    }
    function selectedIds() {
        return Object.keys(selected).filter(function (k) { return selected[k]; }).map(Number);
    }
    function updateSelectionUI() {
        var ids = selectedIds();
        $('tkSelCount').textContent = ids.length;
        $('tkCleanSelCount').textContent = ids.length;
        $('tkCleanSelectedBtn').style.display = (board.is_admin && ids.length) ? '' : 'none';
        $('tkDeleteSelectedBtn').disabled = ids.length === 0;
        var boxes = document.querySelectorAll('.tk-select');
        var allChecked = boxes.length > 0;
        boxes.forEach(function (cb) { if (!cb.checked) allChecked = false; });
        var all = $('tkSelectAll');
        if (all) { all.checked = allChecked; all.disabled = boxes.length === 0; }
    }
    function esc(s) {
        return String(s == null ? '' : s).replace(/[&<>"']/g, function (c) {
            return { '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }[c];
        });
    }

    // ---------------------------------------------------------------- sound
    var alarmTimer = null;
    function alarmStart() {
        if (window.AMSTaskerSiren) window.AMSTaskerSiren.start();
        updateSoundButton();
        if (alarmTimer) return;
        // Browsers only allow audio after a user gesture — keep retrying.
        alarmTimer = setInterval(function () {
            if (window.AMSTaskerSiren) window.AMSTaskerSiren.start();
            updateSoundButton();
        }, 1000);
    }
    function alarmStop() {
        if (alarmTimer) { clearInterval(alarmTimer); alarmTimer = null; }
        if (window.AMSTaskerSiren) window.AMSTaskerSiren.stop();
        updateSoundButton();
    }
    function updateSoundButton() {
        var btn = $('tkSoundBtn');
        if (!btn) return;
        var on = window.AMSTaskerSiren && window.AMSTaskerSiren.isRunning();
        btn.style.display = on ? 'none' : '';
        btn.innerHTML = on
            ? '<i class="bi bi-volume-up-fill me-1"></i> Siren ON'
            : '<i class="bi bi-volume-mute-fill me-1"></i> Enable siren';
    }
    // Browsers block audio until the first interaction — unlock on any click/key.
    ['pointerdown', 'keydown'].forEach(function (ev) {
        document.addEventListener(ev, function () {
            if (!window.AMSTaskerSiren || !alarmTimer) return;
            window.AMSTaskerSiren.unlock().then(function () {
                window.AMSTaskerSiren.start();
                updateSoundButton();
            });
        }, { passive: true });
    });

    // ------------------------------------------------------------- countdown
    function fmtDelta(ms) {
        var neg = ms < 0; ms = Math.abs(ms);
        var m = Math.floor(ms / 60000);
        var d = Math.floor(m / 1440); m -= d * 1440;
        var h = Math.floor(m / 60); m -= h * 60;
        var s = Math.floor((ms % 60000) / 1000);
        var out = d > 0 ? (d + 'd ' + h + 'h') : (h > 0 ? (h + 'h ' + m + 'm') : (m + 'm ' + s + 's'));
        return neg ? ('+' + out + ' OVERDUE') : ('− ' + out);
    }
    function tick() {
        var now = Date.now();
        var anyLiveOverdue = false;
        document.querySelectorAll('.tk-count[data-dl]').forEach(function (el) {
            var dl = Date.parse(el.getAttribute('data-dl'));
            if (isNaN(dl)) { el.textContent = '—'; return; }
            if (dl < now && el.getAttribute('data-live-alarm') === '1') anyLiveOverdue = true;
            var txt = fmtDelta(dl - now);
            el.textContent = txt;
            el.classList.toggle('tk-count-danger', dl < now);
            el.classList.toggle('tk-count-warn', dl >= now && (dl - now) < 30 * 60000);
        });
        // Start the siren the second a live deadline passes, without waiting
        // for the next board poll.
        if (anyLiveOverdue) alarmStart();
    }

    // ----------------------------------------------------------------- api
    function api(url, opts) {
        opts = opts || {};
        if (opts.body) {
            opts.headers = { 'Content-Type': 'application/json' };
            opts.body = JSON.stringify(opts.body);
        }
        return fetch(url, opts).then(function (r) {
            if (r.status === 403) return r.json().then(function (j) { throw new Error(j.error || 'Not allowed'); });
            return r.json();
        });
    }

    // --------------------------------------------------------------- board
    function statusChip(t) {
        var chips = '<span class="tk-chip tk-chip-' + esc(t.status) + '">' + esc(t.status) + '</span> ';
        if (t.note_overdue) chips += '<span class="tk-chip tk-chip-OVERDUE">NOTE LATE</span> ';
        if (t.delivery_overdue) chips += '<span class="tk-chip tk-chip-OVERDUE">DELIVERY LATE</span>';
        return chips;
    }
    function itemsSummary(t) {
        var items = t.items || [];
        if (!items.length) return '—';
        var first = items[0];
        var txt = esc(first.material_name) + ' ×' + esc(first.qty) + ' ' + esc(first.unit || '');
        if (first.warranty) txt += ' <span class="text-secondary">(wty: ' + esc(first.warranty) + ')</span>';
        if (items.length > 1) {
            txt += ' <span class="tk-link tk-detail-toggle" data-id="' + t.id + '">+' + (items.length - 1) + ' more</span>';
        }
        return txt;
    }
    function lastAction(t) {
        var s = t.stamps || {};
        var order = [
            ['delivered_at', 'Delivered', s.delivered_by],
            ['dispatched_at', 'Dispatched', s.dispatched_by],
            ['loaded_at', 'Loaded', s.loaded_by],
            ['noted_at', 'Noted', s.noted_by],
        ];
        for (var i = 0; i < order.length; i++) {
            if (order[i][0] && s[order[i][0]]) {
                return order[i][1] + ' by ' + esc(order[i][2] || '?') +
                    '<div class="text-secondary" style="font-size:.72rem;">' + esc(String(s[order[i][0]]).replace('T', ' ')) + '</div>';
            }
        }
        return '<span class="text-secondary">created by ' + esc(t.created_by || '?') + '</span>' +
            '<div class="text-secondary" style="font-size:.72rem;">' + esc(String(t.created_at || '').replace('T', ' ')) + '</div>';
    }
    function actionButtons(t) {
        var btns = [];
        var next = { NEW: ['noted', 'Noted', 'btn-info'], NOTED: ['loaded', 'Loaded', 'btn-warning'],
                     LOADED: ['dispatched', 'Dispatched', 'btn-primary'], DISPATCHED: ['delivered', 'Delivered', 'btn-success'] }[t.status];
        if (next) {
            btns.push('<button class="btn btn-sm ' + next[2] + ' fw-bold tk-act" data-id="' + t.id +
                '" data-action="' + next[0] + '">' + next[1] + '</button>');
        }
        if (board.is_admin) {
            btns.push('<button class="btn btn-sm btn-outline-warning tk-reset" data-id="' + t.id + '">Reset time</button>');
            if (t.status !== 'CANCELLED' && t.status !== 'DELIVERED') {
                btns.push('<button class="btn btn-sm btn-outline-danger tk-act" data-id="' + t.id + '" data-action="cancel">Cancel</button>');
            }
            if (t.status === 'CANCELLED') {
                btns.push('<button class="btn btn-sm btn-outline-success tk-act" data-id="' + t.id + '" data-action="reopen">Reopen</button>');
            }
            btns.push('<button class="btn btn-sm btn-outline-danger tk-del" data-id="' + t.id + '" title="Delete task (admin only)"><i class="bi bi-trash3"></i></button>');
        }
        btns.push('<button class="btn btn-sm btn-outline-light tk-detail-toggle" data-id="' + t.id + '">Detail</button>');
        return btns.join(' ');
    }
    function detailRow(t) {
        var items = (t.items || []).map(function (it, i) {
            return '<tr><td>' + (i + 1) + '</td><td>' + esc(it.material_name) +
                (it.material_code ? ' <span class="text-secondary">(' + esc(it.material_code) + ')</span>' : '') +
                '</td><td class="tk-item-qty">' + esc(it.qty) + ' ' + esc(it.unit || '') + '</td><td>' +
                esc(it.warranty || '—') + '</td><td>' + esc(it.note || '') + '</td></tr>';
        }).join('');
        var events = (t.events || []).slice().reverse().map(function (e) {
            return '<div class="tk-timeline-item"><strong>' + esc(e.event) + '</strong> — ' +
                esc(e.by_user || 'system') + ' <span class="text-secondary">· ' + esc(String(e.at || '').replace('T', ' ')) + '</span>' +
                (e.detail ? '<div class="text-secondary">' + esc(e.detail) + '</div>' : '') + '</div>';
        }).join('');
        var resets = (t.deadline_resets || []).map(function (r) {
            return '<div style="font-size:.78rem;">' + esc(r.kind) + ': ' + esc(String(r.old_deadline || '—').replace('T', ' ')) +
                ' → ' + esc(String(r.new_deadline || 'cleared').replace('T', ' ')) + ' by ' + esc(r.by_user || '?') +
                (r.reason ? ' (' + esc(r.reason) + ')' : '') + '</div>';
        }).join('');
        var colspan = board.is_admin ? 9 : 8;
        return '<tr class="tk-detail-row" data-for="' + t.id + '"><td colspan="' + colspan + '"><div class="tk-detail-box">' +
            '<div class="row g-3"><div class="col-md-6">' +
            '<div class="fw-bold mb-1">Items</div><table class="table table-dark table-sm mb-0"><thead><tr><th>#</th><th>Material / brand</th><th>Qty</th><th>Warranty</th><th>Note</th></tr></thead><tbody>' +
            items + '</tbody></table>' +
            (t.task_note ? '<div class="mt-2 text-secondary" style="font-size:.82rem;"><i class="bi bi-sticky me-1"></i>' + esc(t.task_note) + '</div>' : '') +
            (resets ? '<div class="mt-2"><div class="fw-bold" style="font-size:.82rem;">Deadline resets (admin)</div>' + resets + '</div>' : '') +
            '</div><div class="col-md-6"><div class="fw-bold mb-1">Timeline</div><div class="tk-timeline">' + (events || '—') + '</div></div>' +
            '</div></div></td></tr>';
    }

    function render() {
        var c = board.counters || {};
        $('tkcTotal').textContent = c.total || 0;
        $('tkcNew').textContent = c.new || 0;
        $('tkcProgress').textContent = (c.noted || 0) + (c.loaded || 0) + (c.dispatched || 0);
        $('tkcDelivered').textContent = c.delivered || 0;
        $('tkcOverdue').textContent = c.overdue || 0;

        if (c.overdue > 0) {
            var labels = (board.overdue_tasks || []).map(function (t) {
                return t.task_no + ' (' + (t.kinds || []).join('+') + ')';
            });
            $('tkAlarmText').textContent = c.overdue + ' deadline(s) missed — ' +
                (labels.length ? labels.join(', ') : 'admin must reset the time');
            if (document.getElementById('amsTaskerAlarmBanner')) {
                // The fixed global banner already carries the message and the
                // sound button; avoid showing the same alarm twice.
                $('tkAlarmBar').style.display = 'none';
            } else {
                $('tkAlarmBar').style.display = 'flex';
            }
            alarmStart();
        } else {
            $('tkAlarmBar').style.display = 'none';
            alarmStop();
        }
        $('tkNewTaskBtn').style.display = board.is_admin ? '' : 'none';
        $('tkCleanBtn').style.display = board.is_admin ? '' : 'none';
        $('tkTestSirenBtn').style.display = board.is_admin ? '' : 'none';
        if (board.cleanup && board.cleanup.deleted > 0) {
            cleanNoticeUntil = Date.now() + 60000;
            cleanNoticeText = 'Auto-clean removed ' + board.cleanup.deleted +
                ' closed task(s). See Clean up → Recent cleanups for the permanent log.';
        }
        if (cleanNoticeText && Date.now() < cleanNoticeUntil) {
            $('tkCleanNotice').style.display = 'block';
            $('tkCleanNotice').textContent = cleanNoticeText;
        } else {
            $('tkCleanNotice').style.display = 'none';
        }

        var body = $('tkBoardBody');
        var colCount = board.is_admin ? 9 : 8;
        if (!board.tasks.length) {
            body.innerHTML = '<tr><td colspan="' + colCount + '" class="text-center text-secondary py-4">No tasks' +
                (filter !== 'all' || searchQ ? ' match this filter.' : ' yet.') + '</td></tr>';
            updateSelectionUI();
            return;
        }
        body.innerHTML = board.tasks.map(function (t) {
            var overdue = t.note_overdue || t.delivery_overdue;
            var row = '<tr class="' + (overdue ? 'tk-row-overdue' : '') + '" data-row="' + t.id + '">' +
                (board.is_admin ? '<td><input type="checkbox" class="tk-select" data-id="' + t.id + '"' +
                    (selected[t.id] ? ' checked' : '') + '></td>' : '') +
                '<td><span class="fw-bold tk-link tk-detail-toggle" data-id="' + t.id + '">' + esc(t.task_no) + '</span>' +
                '<div class="text-secondary" style="font-size:.72rem;">#' + esc(t.id) + ' · resets: ' + esc(t.deadline_reset_count || 0) + '</div></td>' +
                '<td><div class="fw-semibold">' + esc(t.client_name) + '</div>' +
                (t.client_code ? '<div class="text-secondary" style="font-size:.74rem;">' + esc(t.client_code) + '</div>' : '') + '</td>' +
                '<td style="max-width:240px;">' + itemsSummary(t) + '</td>' +
                '<td>' + (t.note_deadline_at
                    ? '<span class="tk-count" data-dl="' + esc(t.note_deadline_at) + '" data-live-alarm="' +
                      (t.status === 'NEW' ? '1' : '0') + '"></span>'
                    : '<span class="text-secondary">—</span>') +
                (t.note_overdue ? ' <i class="bi bi-bell-fill text-danger"></i>' : '') + '</td>' +
                '<td>' + (t.delivery_deadline_at
                    ? '<span class="tk-count" data-dl="' + esc(t.delivery_deadline_at) + '" data-live-alarm="' +
                      ((t.status !== 'DELIVERED' && t.status !== 'CANCELLED') ? '1' : '0') + '"></span>'
                    : '<span class="text-secondary">—</span>') +
                (t.delivery_overdue ? ' <i class="bi bi-bell-fill text-danger"></i>' : '') + '</td>' +
                '<td>' + statusChip(t) + '</td>' +
                '<td style="font-size:.8rem;">' + lastAction(t) + '</td>' +
                '<td class="text-end text-nowrap">' + actionButtons(t) + '</td></tr>';
            if (expanded[t.id]) row += detailRow(t);
            return row;
        }).join('');
        updateSelectionUI();
        tick();
    }

    function loadBoard() {
        var url = '/tasker/api/board?filter=' + encodeURIComponent(filter) +
            (searchQ ? '&q=' + encodeURIComponent(searchQ) : '');
        api(url).then(function (data) {
            board = data;
            render();
            // refresh open detail rows
            Object.keys(expanded).forEach(function (id) {
                if (expanded[id]) loadDetail(id, true);
            });
        }).catch(function () { /* transient — next poll retries */ });
    }

    function loadDetail(id, keepOpen) {
        api('/tasker/api/tasks/' + id).then(function (t) {
            expanded[id] = true;
            // replace the task in board with full data for rendering detail
            for (var i = 0; i < board.tasks.length; i++) {
                if (String(board.tasks[i].id) === String(id)) { board.tasks[i] = t; break; }
            }
            if (keepOpen) {
                var existing = document.querySelector('.tk-detail-row[data-for="' + id + '"]');
                if (existing) existing.outerHTML = detailRow(t);
            } else {
                render();
            }
        }).catch(function () {});
    }

    // ------------------------------------------------------------- combo box
    function makeCombo(input, list, url, onPick, renderRow) {
        var active = -1, items = [], timer = null;
        function hide() { list.style.display = 'none'; active = -1; }
        function show() {
            if (!items.length) { hide(); return; }
            list.innerHTML = items.map(function (it, i) {
                return '<div class="tk-combo-item' + (i === active ? ' tk-active' : '') + '" data-i="' + i + '">' +
                    renderRow(it) + '</div>';
            }).join('');
            list.style.display = 'block';
        }
        function fetchList() {
            var q = input.value.trim();
            api(url + '?q=' + encodeURIComponent(q)).then(function (rows) {
                items = rows || []; active = items.length ? 0 : -1; show();
            }).catch(function () { items = []; show(); });
        }
        input.addEventListener('input', function () {
            clearTimeout(timer); timer = setTimeout(fetchList, 220);
        });
        input.addEventListener('focus', fetchList);
        input.addEventListener('keydown', function (e) {
            if (list.style.display === 'none') { if (e.key === 'ArrowDown') fetchList(); return; }
            if (e.key === 'ArrowDown') { active = Math.min(active + 1, items.length - 1); show(); e.preventDefault(); }
            else if (e.key === 'ArrowUp') { active = Math.max(active - 1, 0); show(); e.preventDefault(); }
            else if (e.key === 'Enter') {
                if (active >= 0 && items[active]) { onPick(items[active]); hide(); e.preventDefault(); }
            } else if (e.key === 'Escape') { hide(); }
        });
        list.addEventListener('mousedown', function (e) {
            var el = e.target.closest('.tk-combo-item');
            if (!el) return;
            var it = items[parseInt(el.getAttribute('data-i'), 10)];
            if (it) { onPick(it); hide(); }
            e.preventDefault();
        });
        input.addEventListener('blur', function () { setTimeout(hide, 150); });
    }

    // ---------------------------------------------------------- new task modal
    var selectedClient = null;
    var itemSeq = 0;
    var createRequestId = null;

    function localInputValue(d) {
        var pad = function (n) { return String(n).padStart(2, '0'); };
        return d.getFullYear() + '-' + pad(d.getMonth() + 1) + '-' + pad(d.getDate()) +
            'T' + pad(d.getHours()) + ':' + pad(d.getMinutes());
    }

    function addItemRow() {
        itemSeq += 1;
        var div = document.createElement('div');
        div.className = 'row g-2 align-items-end tk-item-row mb-2';
        div.innerHTML =
            '<div class="col-md-4 tk-combo-wrap">' +
            '  <input class="form-control tk-item-name" autocomplete="off" placeholder="Material / brand…" data-row="' + itemSeq + '">' +
            '  <div class="tk-combo-list"></div>' +
            '</div>' +
            '<div class="col-4 col-md-2"><input type="number" step="any" min="0" class="form-control tk-item-qty-in" placeholder="Qty" value=""></div>' +
            '<div class="col-4 col-md-2"><input class="form-control tk-item-unit" placeholder="Unit"></div>' +
            '<div class="col-4 col-md-2"><input class="form-control tk-item-wty" placeholder="Warranty"></div>' +
            '<div class="col-4 col-md-1"><input class="form-control tk-item-note" placeholder="Note"></div>' +
            '<div class="col-4 col-md-1"><button type="button" class="btn btn-sm btn-outline-danger tk-item-remove w-100"><i class="bi bi-x-lg"></i></button></div>';
        $('tkItemRows').appendChild(div);
        var nameInput = div.querySelector('.tk-item-name');
        var list = div.querySelector('.tk-combo-list');
        makeCombo(nameInput, list, '/tasker/api/materials/search', function (m) {
            nameInput.value = m.name;
            nameInput.dataset.ref = m.id;
            nameInput.dataset.code = m.code || '';
            var unitEl = div.querySelector('.tk-item-unit');
            if (!unitEl.value && m.unit) unitEl.value = m.unit;
        }, function (m) {
            return esc(m.name) + '<span class="tk-combo-code">' + esc(m.code || '') + ' · ' + esc(m.unit || '') + '</span>';
        });
        div.querySelector('.tk-item-remove').addEventListener('click', function () {
            div.remove();
        });
    }

    function collectItems() {
        var rows = document.querySelectorAll('.tk-item-row');
        var out = [];
        rows.forEach(function (row) {
            var nameEl = row.querySelector('.tk-item-name');
            var name = nameEl.value.trim();
            if (!name) return;
            out.push({
                material_ref_id: nameEl.dataset.ref ? parseInt(nameEl.dataset.ref, 10) : null,
                material_code: nameEl.dataset.code || '',
                material_name: name,
                qty: parseFloat(row.querySelector('.tk-item-qty-in').value || '0') || 0,
                unit: row.querySelector('.tk-item-unit').value.trim(),
                warranty: row.querySelector('.tk-item-wty').value.trim(),
                note: row.querySelector('.tk-item-note').value.trim(),
            });
        });
        return out;
    }

    function showError(id, msg) {
        var el = $(id);
        el.textContent = msg || '';
        el.style.display = msg ? 'block' : 'none';
    }

    // ------------------------------------------------------------- wiring
    document.addEventListener('DOMContentLoaded', function () {
        // filters
        document.querySelectorAll('.tk-filter-btn').forEach(function (b) {
            b.addEventListener('click', function () {
                document.querySelectorAll('.tk-filter-btn').forEach(function (x) { x.classList.remove('active'); });
                b.classList.add('active');
                filter = b.getAttribute('data-filter');
                loadBoard();
            });
        });
        var searchTimer = null;
        $('tkSearch').addEventListener('input', function (e) {
            clearTimeout(searchTimer);
            searchTimer = setTimeout(function () { searchQ = e.target.value.trim(); loadBoard(); }, 300);
        });

        // board actions (delegated)
        $('tkBoardBody').addEventListener('click', function (e) {
            var act = e.target.closest('.tk-act');
            if (act) {
                var id = act.getAttribute('data-id');
                var action = act.getAttribute('data-action');
                var note = '';
                if (action === 'delivered') {
                    note = window.prompt('Delivery confirmation note (optional):') || '';
                } else if (action === 'cancel') {
                    if (!window.prompt('Type YES to cancel this task (reason next):')) return;
                    note = window.prompt('Cancel reason (optional):') || '';
                }
                act.disabled = true;
                api('/tasker/api/tasks/' + id + '/action', { method: 'POST', body: { action: action, note: note } })
                    .then(function () { loadBoard(); })
                    .catch(function (err) { alert(err.message || 'Action failed'); act.disabled = false; });
                return;
            }
            var del = e.target.closest('.tk-del');
            if (del) {
                openDeleteModal(del.getAttribute('data-id'));
                return;
            }
            var rst = e.target.closest('.tk-reset');
            if (rst) {
                openReset(rst.getAttribute('data-id'));
                return;
            }
            var det = e.target.closest('.tk-detail-toggle');
            if (det) {
                var did = det.getAttribute('data-id');
                if (expanded[did]) {
                    expanded[did] = false;
                    var row = document.querySelector('.tk-detail-row[data-for="' + did + '"]');
                    if (row) row.remove();
                } else {
                    loadDetail(did, false);
                }
            }
        });

        // row selection (admin cleanup)
        $('tkBoardBody').addEventListener('change', function (e) {
            var cb = e.target.closest('.tk-select');
            if (!cb) return;
            var id = cb.getAttribute('data-id');
            if (cb.checked) selected[id] = true;
            else delete selected[id];
            updateSelectionUI();
        });
        var selectAll = $('tkSelectAll');
        if (selectAll) {
            selectAll.addEventListener('change', function () {
                var checked = selectAll.checked;
                document.querySelectorAll('.tk-select').forEach(function (cb) {
                    cb.checked = checked;
                    var id = cb.getAttribute('data-id');
                    if (checked) selected[id] = true;
                    else delete selected[id];
                });
                updateSelectionUI();
            });
        }

        // The global alarm widget owns the fixed red banner; hide the inline
        // bar while it is showing so the message is not duplicated.
        document.addEventListener('tasker:alarm-banner', function (e) {
            if (e.detail && e.detail.active) $('tkAlarmBar').style.display = 'none';
        });

        // sound unlock (browsers require a click before Web Audio can play)
        var soundBtn = $('tkSoundBtn');
        if (soundBtn) {
            soundBtn.addEventListener('click', function () {
                if (!window.AMSTaskerSiren) return;
                window.AMSTaskerSiren.unlock().then(function () {
                    window.AMSTaskerSiren.start();
                    updateSoundButton();
                });
            });
        }
        var testSirenBtn = $('tkTestSirenBtn');
        if (testSirenBtn) {
            testSirenBtn.addEventListener('click', function () {
                if (!window.AMSTaskerSiren) return;
                window.AMSTaskerSiren.unlock().then(function () {
                    window.AMSTaskerSiren.start();
                    updateSoundButton();
                    // A test burst only — never silence a real alarm.
                    setTimeout(function () {
                        if (!alarmTimer) {
                            window.AMSTaskerSiren.stop();
                            updateSoundButton();
                        }
                    }, 2500);
                });
            });
        }

        // new task modal
        $('tkNewTaskBtn').addEventListener('click', function () {
            selectedClient = null;
            // One id per opening of the New Task form: every send from this
            // form carries it, so a double click or a retry cannot create a
            // second task (the server returns the one already made).
            createRequestId = 'rq-' + Date.now().toString(36) + '-' + Math.random().toString(36).slice(2, 10);
            $('tkClientInput').value = '';
            $('tkClientHint').textContent = 'Start typing to search…';
            $('tkItemRows').innerHTML = '';
            addItemRow();
            var now = new Date();
            $('tkNoteDeadline').value = localInputValue(new Date(now.getTime() + 30 * 60000));
            $('tkDeliveryDeadline').value = localInputValue(new Date(now.getTime() + 4 * 3600000));
            $('tkTaskNote').value = '';
            showError('tkCreateError', '');
            new bootstrap.Modal($('tkNewModal')).show();
        });
        makeCombo($('tkClientInput'), $('tkClientList'), '/tasker/api/clients/search', function (c) {
            selectedClient = c;
            $('tkClientInput').value = c.name;
            $('tkClientHint').innerHTML = '<i class="bi bi-check-circle-fill text-success"></i> Selected: ' +
                esc(c.name) + (c.code ? ' (' + esc(c.code) + ')' : '');
        }, function (c) {
            return esc(c.name) + '<span class="tk-combo-code">' + esc(c.code || '') + '</span>';
        });
        $('tkAddItem').addEventListener('click', addItemRow);

        document.querySelectorAll('.tk-quick-chip').forEach(function (chip) {
            chip.addEventListener('click', function () {
                var d = new Date();
                if (chip.dataset.min) d = new Date(d.getTime() + parseInt(chip.dataset.min, 10) * 60000);
                else if (chip.dataset.eod) d.setHours(20, 0, 0, 0);
                else if (chip.dataset.tomorrow) { d.setDate(d.getDate() + 1); d.setHours(10, 0, 0, 0); }
                var target = chip.dataset.kind === 'note' ? $('tkNoteDeadline') : $('tkDeliveryDeadline');
                target.value = localInputValue(d);
            });
        });

        $('tkCreateSave').addEventListener('click', function () {
            showError('tkCreateError', '');
            if (!selectedClient) { showError('tkCreateError', 'Please pick a client from the search list.'); return; }
            var items = collectItems();
            if (!items.length) { showError('tkCreateError', 'Add at least one item.'); return; }
            if (!$('tkNoteDeadline').value || !$('tkDeliveryDeadline').value) {
                showError('tkCreateError', 'Both deadlines are required.'); return;
            }
            var btn = this; btn.disabled = true;
            api('/tasker/api/tasks', {
                method: 'POST',
                body: {
                    client_ref_id: selectedClient.id,
                    client_code: selectedClient.code || '',
                    client_name: selectedClient.name,
                    items: items,
                    note_deadline_at: $('tkNoteDeadline').value,
                    delivery_deadline_at: $('tkDeliveryDeadline').value,
                    task_note: $('tkTaskNote').value.trim(),
                    request_id: createRequestId || '',
                }
            }).then(function () {
                btn.disabled = false;
                bootstrap.Modal.getInstance($('tkNewModal')).hide();
                loadBoard();
            }).catch(function (err) {
                btn.disabled = false;
                showError('tkCreateError', err.message || 'Could not create task.');
            });
        });

        // reset modal
        $('tkResetSave').addEventListener('click', function () {
            var id = $('tkResetModal').dataset.taskId;
            showError('tkResetError', '');
            var body = { reason: $('tkResetReason').value.trim() };
            if ($('tkResetNoteClear').checked) body.note_deadline_at = null;
            else if ($('tkResetNote').value) body.note_deadline_at = $('tkResetNote').value;
            if ($('tkResetDeliveryClear').checked) body.delivery_deadline_at = null;
            else if ($('tkResetDelivery').value) body.delivery_deadline_at = $('tkResetDelivery').value;
            if (!('note_deadline_at' in body) && !('delivery_deadline_at' in body)) {
                showError('tkResetError', 'Set at least one new deadline (or tick a clear box).'); return;
            }
            var btn = this; btn.disabled = true;
            api('/tasker/api/tasks/' + id + '/reset_deadline', { method: 'POST', body: body })
                .then(function () {
                    btn.disabled = false;
                    bootstrap.Modal.getInstance($('tkResetModal')).hide();
                    loadBoard();
                }).catch(function (err) {
                    btn.disabled = false;
                    showError('tkResetError', err.message || 'Reset failed.');
                });
        });

        // ---------------------------------------------------- cleanup (admin)
        $('tkCleanBtn').addEventListener('click', openCleanModal);
        $('tkCleanSelectedBtn').addEventListener('click', function () {
            openCleanModal();
            setTimeout(function () { $('tkDeleteSelectedBtn').focus(); }, 450);
        });

        $('tkDeleteConfirm').addEventListener('input', function () {
            $('tkDeleteSave').disabled = this.value.trim().toUpperCase() !== 'DELETE';
        });
        $('tkDeleteSave').addEventListener('click', function () {
            var id = $('tkDeleteModal').dataset.taskId;
            showError('tkDeleteError', '');
            var btn = this; btn.disabled = true;
            api('/tasker/api/tasks/' + id + '/delete', {
                method: 'POST',
                body: { confirm: 'DELETE', reason: $('tkDeleteReason').value.trim() },
            }).then(function () {
                btn.disabled = false;
                bootstrap.Modal.getInstance($('tkDeleteModal')).hide();
                delete selected[id];
                loadBoard();
            }).catch(function (err) {
                btn.disabled = false;
                showError('tkDeleteError', err.message || 'Delete failed.');
            });
        });

        $('tkDeleteSelectedBtn').addEventListener('click', function () {
            var ids = selectedIds();
            if (!ids.length) return;
            var answer = window.prompt('Type DELETE to permanently delete ' + ids.length + ' selected task(s):');
            if (!answer || answer.trim().toUpperCase() !== 'DELETE') return;
            showError('tkSelectedError', '');
            var btn = this; btn.disabled = true;
            api('/tasker/api/tasks/bulk_delete', {
                method: 'POST',
                body: {
                    task_ids: ids,
                    confirm: 'DELETE',
                    reason: $('tkSelectedReason').value.trim(),
                },
            }).then(function (data) {
                btn.disabled = false;
                selected = {};
                updateSelectionUI();
                if (data.deleted) {
                    $('tkSelectedError').className = 'text-success mt-2';
                    $('tkSelectedError').textContent = data.deleted + ' task(s) deleted permanently.';
                    $('tkSelectedError').style.display = 'block';
                }
                loadBoard();
                loadCleanSettings();
            }).catch(function (err) {
                btn.disabled = false;
                $('tkSelectedError').className = 'text-danger mt-2';
                $('tkSelectedError').textContent = err.message || 'Delete failed.';
                $('tkSelectedError').style.display = 'block';
            });
        });

        $('tkSaveSettings').addEventListener('click', function () {
            var btn = this; btn.disabled = true;
            api('/tasker/api/settings', {
                method: 'POST',
                body: {
                    auto_clean_enabled: $('tkAutoCleanEnabled').checked,
                    auto_clean_days: parseInt($('tkAutoCleanDays').value, 10) || 30,
                    auto_clean_include_cancelled: $('tkAutoCleanCancelled').checked,
                },
            }).then(function (data) {
                btn.disabled = false;
                $('tkSettingsMsg').className = 'text-success';
                $('tkSettingsMsg').textContent = 'Saved — auto-clean is ' +
                    (data.settings.auto_clean_enabled ? 'ON' : 'OFF') + '.';
            }).catch(function (err) {
                btn.disabled = false;
                $('tkSettingsMsg').className = 'text-danger';
                $('tkSettingsMsg').textContent = err.message || 'Save failed.';
            });
        });

        $('tkCleanPreviewBtn').addEventListener('click', function () {
            showError('tkCleanError', '');
            var btn = this; btn.disabled = true;
            api('/tasker/api/cleanup', { method: 'POST', body: cleanupBody(true) }).then(function (data) {
                btn.disabled = false;
                $('tkCleanPreview').className = data.matched ? 'mt-2 text-warning' : 'mt-2 text-secondary';
                $('tkCleanPreview').innerHTML = '<strong>' + data.matched + '</strong> closed task(s) match.' +
                    (data.matched ? ' Press Delete matched to remove them permanently.' : '');
            }).catch(function (err) {
                btn.disabled = false;
                showError('tkCleanError', err.message || 'Preview failed.');
            });
        });

        $('tkCleanConfirm').addEventListener('input', function () {
            $('tkCleanRunBtn').disabled = this.value.trim().toUpperCase() !== 'DELETE';
        });
        // Changing the sweep after typing DELETE must re-arm the confirmation.
        ['tkCleanDays', 'tkCleanDelivered', 'tkCleanCancelled'].forEach(function (id) {
            $(id).addEventListener('input', function () {
                $('tkCleanConfirm').value = '';
                $('tkCleanRunBtn').disabled = true;
                $('tkCleanPreview').className = 'mt-2 text-secondary';
                $('tkCleanPreview').textContent = 'Press Preview to see how many closed tasks match.';
            });
            $(id).addEventListener('change', function () {
                $('tkCleanConfirm').value = '';
                $('tkCleanRunBtn').disabled = true;
                $('tkCleanPreview').className = 'mt-2 text-secondary';
                $('tkCleanPreview').textContent = 'Press Preview to see how many closed tasks match.';
            });
        });
        $('tkCleanRunBtn').addEventListener('click', function () {
            showError('tkCleanError', '');
            var btn = this; btn.disabled = true;
            api('/tasker/api/cleanup', { method: 'POST', body: cleanupBody(false) }).then(function (data) {
                btn.disabled = false;
                $('tkCleanPreview').className = 'mt-2 text-success';
                $('tkCleanPreview').innerHTML = '<strong>' + data.deleted + '</strong> task(s) deleted permanently.';
                $('tkCleanConfirm').value = '';
                $('tkCleanRunBtn').disabled = true;
                loadCleanSettings();
                loadBoard();
            }).catch(function (err) {
                btn.disabled = false;
                showError('tkCleanError', err.message || 'Cleanup failed.');
            });
        });

        // live updates
        loadBoard();
        tickTimer = setInterval(tick, 1000);
        pollTimer = setInterval(function () {
            if (!document.hidden) loadBoard();
        }, 10000);
        document.addEventListener('visibilitychange', function () {
            if (!document.hidden) loadBoard();
        });
    });

    // ------------------------------------------------------- cleanup helpers
    function cleanupBody(dryRun) {
        var statuses = [];
        if ($('tkCleanDelivered').checked) statuses.push('DELIVERED');
        if ($('tkCleanCancelled').checked) statuses.push('CANCELLED');
        return {
            statuses: statuses,
            older_than_days: parseInt($('tkCleanDays').value, 10) || 0,
            dry_run: !!dryRun,
            reason: $('tkCleanReason').value.trim(),
            confirm: dryRun ? undefined : 'DELETE',
        };
    }

    function openDeleteModal(id) {
        var t = findTask(id);
        $('tkDeleteModal').dataset.taskId = id;
        $('tkDeleteTaskNo').textContent = t ? t.task_no : ('#' + id);
        $('tkDeleteSummary').innerHTML = t
            ? '<div><strong>' + esc(t.client_name || '—') + '</strong> · ' + statusChip(t) + '</div>' +
              '<div class="text-secondary">Items: ' + (t.items || []).length +
              ' · deadline resets: ' + esc(t.deadline_reset_count || 0) + '</div>'
            : '';
        $('tkDeleteReason').value = '';
        $('tkDeleteConfirm').value = '';
        $('tkDeleteSave').disabled = true;
        showError('tkDeleteError', '');
        new bootstrap.Modal($('tkDeleteModal')).show();
    }

    function openCleanModal() {
        updateSelectionUI();
        $('tkCleanPreview').className = 'mt-2 text-secondary';
        $('tkCleanPreview').textContent = 'Press Preview to see how many closed tasks match.';
        $('tkCleanConfirm').value = '';
        $('tkCleanRunBtn').disabled = true;
        $('tkCleanError').style.display = 'none';
        $('tkSelectedError').style.display = 'none';
        $('tkSelectedError').textContent = '';
        $('tkSettingsMsg').textContent = '';
        $('tkSettingsMsg').className = 'text-secondary';
        $('tkRecentCleanups').textContent = 'Loading…';
        new bootstrap.Modal($('tkCleanModal')).show();
        loadCleanSettings();
    }

    function loadCleanSettings() {
        api('/tasker/api/settings').then(function (data) {
            var s = data.settings || {};
            $('tkAutoCleanEnabled').checked = !!s.auto_clean_enabled;
            $('tkAutoCleanDays').value = s.auto_clean_days || 30;
            $('tkAutoCleanCancelled').checked = s.auto_clean_include_cancelled !== false;
            var rows = data.recent_cleanups || [];
            $('tkRecentCleanups').innerHTML = rows.map(function (c) {
                return '<div class="tk-recent-cleanup"><strong>' + esc(c.task_no) + '</strong> · ' +
                    esc(c.client_name || '—') + ' · ' + esc(c.status || '') +
                    ' <span class="text-secondary">— removed by ' + esc(c.deleted_by || '—') +
                    ' · ' + esc(String(c.deleted_at || '').replace('T', ' ')) + '</span>' +
                    (c.reason ? '<div class="text-secondary">' + esc(c.reason) + '</div>' : '') +
                    '</div>';
            }).join('') || '<div class="text-secondary">No cleanups yet.</div>';
        }).catch(function () {
            $('tkRecentCleanups').textContent = 'Could not load cleanup history.';
        });
    }

    function openReset(id) {
        var t = null;
        for (var i = 0; i < board.tasks.length; i++) {
            if (String(board.tasks[i].id) === String(id)) { t = board.tasks[i]; break; }
        }
        $('tkResetModal').dataset.taskId = id;
        $('tkResetTaskNo').textContent = t ? t.task_no : ('#' + id);
        $('tkResetNote').value = t && t.note_deadline_at ? localInputValue(new Date(t.note_deadline_at)) : '';
        $('tkResetDelivery').value = t && t.delivery_deadline_at ? localInputValue(new Date(t.delivery_deadline_at)) : '';
        $('tkResetNoteClear').checked = false;
        $('tkResetDeliveryClear').checked = false;
        $('tkResetReason').value = '';
        showError('tkResetError', '');
        new bootstrap.Modal($('tkResetModal')).show();
    }
})();
