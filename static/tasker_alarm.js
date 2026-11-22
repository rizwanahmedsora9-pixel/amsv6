/* Tasker global alarm widget — loaded on every AMS page (layout.html).
 *
 * Strictly one-way: it only POLLS /tasker/api/alarm_state (read-only) and
 * reacts visually/audibly.  It never writes anything and is fully dormant
 * when no Tasker deadline is overdue.  The board page (tasker.js) runs its
 * own alarm and sets window.TASKER_PAGE_ACTIVE so this widget backs off.
 */
(function () {
    'use strict';

    var POLL_MS = 20000;
    var timer = null;
    var alarmTimer = null;
    var badge = null;

    function beep() {
        try {
            var Ctx = window.AudioContext || window.webkitAudioContext;
            if (!Ctx) return;
            if (!beep.ctx) beep.ctx = new Ctx();
            var ctx = beep.ctx;
            if (ctx.state === 'suspended') ctx.resume();
            var o = ctx.createOscillator(), g = ctx.createGain();
            o.type = 'square'; o.frequency.value = 880;
            o.connect(g); g.connect(ctx.destination);
            var t = ctx.currentTime;
            g.gain.setValueAtTime(0.08, t);
            g.gain.setValueAtTime(0, t + 0.12);
            g.gain.setValueAtTime(0.08, t + 0.20);
            g.gain.setValueAtTime(0, t + 0.32);
            o.start(t); o.stop(t + 0.35);
        } catch (e) { /* audio blocked — the badge still alerts */ }
    }
    function alarmStart() {
        if (alarmTimer || window.TASKER_PAGE_ACTIVE) return;
        beep();
        alarmTimer = setInterval(function () {
            if (!window.TASKER_PAGE_ACTIVE) beep();
        }, 2500);
    }
    function alarmStop() {
        if (alarmTimer) { clearInterval(alarmTimer); alarmTimer = null; }
    }

    function ensureBadge() {
        if (badge && badge.isConnected) return badge;
        badge = document.createElement('a');
        badge.href = '/tasker';
        badge.id = 'amsTaskerAlarmBadge';
        badge.style.cssText = [
            'position:fixed', 'top:14px', 'right:14px', 'z-index:2147483000',
            'background:#dc3545', 'color:#fff', 'font-weight:700', 'font-size:.85rem',
            'padding:10px 16px', 'border-radius:999px', 'text-decoration:none',
            'box-shadow:0 0 18px rgba(220,53,69,.65)', 'cursor:pointer',
            'font-family:inherit', 'line-height:1.2',
        ].join(';');
        badge.style.animation = 'amsTaskerPulse 1.6s ease-in-out infinite';
        if (!document.getElementById('amsTaskerAlarmStyle')) {
            var st = document.createElement('style');
            st.id = 'amsTaskerAlarmStyle';
            st.textContent = '@keyframes amsTaskerPulse{0%,100%{transform:scale(1);opacity:1}' +
                '50%{transform:scale(1.06);opacity:.82}}';
            document.head.appendChild(st);
        }
        document.body.appendChild(badge);
        return badge;
    }
    function removeBadge() {
        if (badge && badge.isConnected) badge.remove();
    }

    function refresh() {
        fetch('/tasker/api/alarm_state', { credentials: 'same-origin' })
            .then(function (r) {
                if (!r.ok) throw new Error('alarm_state unavailable');
                var ct = r.headers.get('content-type') || '';
                if (ct.indexOf('json') === -1) throw new Error('not json');
                return r.json();
            })
            .then(function (data) {
                if (document.hidden && !(data && data.overdue_count > 0)) return;
                if (data && data.overdue_count > 0) {
                    var b = ensureBadge();
                    b.textContent = '⚠ TASKER ALARM — ' + data.overdue_count + ' deadline missed';
                    b.title = (data.tasks || []).map(function (t) {
                        return t.task_no + ' · ' + t.client_name + ' · ' + t.kind + ' deadline';
                    }).join('\n');
                    alarmStart();
                } else {
                    removeBadge();
                    alarmStop();
                }
            })
            .catch(function () { /* logged out or transient — stay quiet */ });
    }

    function boot() {
        refresh();
        timer = setInterval(function () {
            if (!document.hidden) refresh();
        }, POLL_MS);
        document.addEventListener('visibilitychange', function () {
            if (!document.hidden) refresh();
        });
    }

    if (document.readyState === 'loading') {
        document.addEventListener('DOMContentLoaded', boot);
    } else {
        boot();
    }
})();
