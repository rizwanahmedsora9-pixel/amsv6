/* Tasker global alarm widget — loaded on every AMS page (layout.html).
 *
 * Polls /tasker/api/alarm_state every 5 seconds.  When any deadline is
 * missed it shows a fixed RED FLASHING BANNER with the task numbers and plays
 * a continuous police-style siren until an admin resets the time.
 *
 * Browsers block Web Audio until the first user gesture, so the banner carries
 * an explicit "Enable siren" button and the siren also unlocks on the first
 * click/keypress anywhere.  The red message itself never depends on audio.
 *
 * The Tasker board page runs its own alarm bar and siren (tasker.js); this
 * widget backs off there to avoid a double banner / double siren.
 */
(function () {
    'use strict';

    var POLL_MS = 5000;
    var timer = null;
    var banner = null;
    var bannerText = null;
    var soundBtn = null;

    // ------------------------------------------------------------- siren
    function siren() {
        return window.AMSTaskerSiren || null;
    }

    // ------------------------------------------------------------ banner
    function ensureStyle() {
        if (document.getElementById('amsTaskerAlarmStyle')) return;
        var st = document.createElement('style');
        st.id = 'amsTaskerAlarmStyle';
        st.textContent =
            '@keyframes amsTaskerFlash{' +
            '0%,100%{background:#7f1d1d;box-shadow:0 0 0 0 rgba(220,38,38,0)}' +
            '50%{background:#dc2626;box-shadow:0 0 30px rgba(220,38,38,.9)}}';
        document.head.appendChild(st);
    }

    function ensureBanner() {
        if (banner && banner.isConnected) return banner;
        ensureStyle();
        banner = document.createElement('div');
        banner.id = 'amsTaskerAlarmBanner';
        banner.style.cssText = [
            'position:fixed', 'top:0', 'left:0', 'right:0',
            'z-index:2147483000', 'box-sizing:border-box',
            'background:#7f1d1d', 'color:#fff', 'border:0',
            'border-bottom:4px solid #fecaca',
            'padding:10px 18px', 'display:flex', 'flex-wrap:wrap',
            'align-items:center', 'gap:12px', 'box-shadow:0 10px 34px rgba(0,0,0,.6)',
            'font-family:inherit', 'line-height:1.3',
            'animation:amsTaskerFlash .75s ease-in-out infinite',
        ].join(';');
        banner.innerHTML =
            '<div style="font-size:1.7rem;line-height:1;">🚨</div>' +
            '<div style="flex:1;min-width:0;">' +
              '<div style="font-weight:800;font-size:1rem;letter-spacing:.02em;">' +
                '⚠ TASKER ALARM — <span id="amsTaskerAlarmText">deadline missed</span>' +
              '</div>' +
              '<div style="font-size:.78rem;opacity:.92;margin-top:2px;">' +
                'Police siren sounds until an admin opens the task and resets the time. ' +
                'Normal users cannot silence it.' +
              '</div>' +
            '</div>' +
            '<button type="button" id="amsTaskerSoundBtn" ' +
              'style="flex-shrink:0;border:0;border-radius:999px;padding:9px 15px;' +
              'font-weight:800;font-size:.78rem;background:#fff;color:#7f1d1d;cursor:pointer;">' +
              '🔊 Enable siren</button>';
        document.body.appendChild(banner);
        document.dispatchEvent(new CustomEvent('tasker:alarm-banner', { detail: { active: true } }));
        bannerText = banner.querySelector('#amsTaskerAlarmText');
        soundBtn = banner.querySelector('#amsTaskerSoundBtn');
        soundBtn.addEventListener('click', function (e) {
            e.stopPropagation();
            var s = siren();
            if (!s) return;
            s.unlock().then(function () {
                s.start();
                updateSoundBtn();
            }).catch(function () {
                soundBtn.textContent = '🔇 Click again to enable';
            });
        });
        updateSoundBtn();
        return banner;
    }

    function updateSoundBtn() {
        if (!soundBtn) return;
        var s = siren();
        if (s && s.isRunning()) {
            soundBtn.textContent = '🔔 Siren ON';
            soundBtn.disabled = true;
            soundBtn.style.opacity = '.85';
        } else {
            soundBtn.textContent = '🔊 Enable siren';
            soundBtn.disabled = false;
            soundBtn.style.opacity = '1';
        }
    }

    function removeBanner() {
        if (banner && banner.isConnected) banner.remove();
        banner = null;
        bannerText = null;
        soundBtn = null;
        document.dispatchEvent(new CustomEvent('tasker:alarm-banner', { detail: { active: false } }));
    }

    function alarmText(data) {
        var tasks = (data && data.tasks) || [];
        if (!tasks.length) return 'deadline missed';
        var grouped = {};
        tasks.forEach(function (t) {
            if (!grouped[t.task_no]) grouped[t.task_no] = [];
            if (grouped[t.task_no].indexOf(t.kind) === -1) grouped[t.task_no].push(t.kind);
        });
        return Object.keys(grouped).map(function (no) {
            return no + ' (' + grouped[no].join('+') + ')';
        }).join(', ');
    }

    // ------------------------------------------------------------ refresh
    function refresh() {
        fetch('/tasker/api/alarm_state', { credentials: 'same-origin' })
            .then(function (r) {
                if (!r.ok) throw new Error('alarm_state unavailable');
                var ct = r.headers.get('content-type') || '';
                if (ct.indexOf('json') === -1) throw new Error('not json');
                return r.json();
            })
            .then(function (data) {
                if (data && data.overdue_count > 0) {
                    ensureBanner();
                    if (bannerText) bannerText.textContent = alarmText(data);
                    var s = siren();
                    if (s) s.start();
                    updateSoundBtn();
                } else {
                    removeBanner();
                    var s2 = siren();
                    if (s2) s2.stop();
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

        // Browsers require a user gesture before audio can play.  Any click or
        // keypress unlocks it and starts the siren when an alarm is active.
        ['pointerdown', 'keydown'].forEach(function (ev) {
            document.addEventListener(ev, function () {
                var s = siren();
                if (!s) return;
                if (s.state() === 'suspended') {
                    s.unlock().then(function () {
                        s.start();
                        updateSoundBtn();
                    }).catch(function () {});
                }
            }, { passive: true });
        });
    }

    if (document.readyState === 'loading') {
        document.addEventListener('DOMContentLoaded', boot);
    } else {
        boot();
    }
})();
