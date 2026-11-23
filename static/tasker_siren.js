/* Tasker police siren — shared by the board page and the global alarm widget.
 *
 * Browsers block Web Audio until the first user gesture.  start() is
 * idempotent and safe to call on every poll; if the context is still suspended
 * it resumes it and retries.  The red flashing message is shown independently
 * of audio, so a blocked siren never hides the alarm.
 */
(function () {
    'use strict';

    var audio = { ctx: null, siren: null, want: false };

    function getCtx() {
        if (audio.ctx) return audio.ctx;
        var Ctx = window.AudioContext || window.webkitAudioContext;
        if (!Ctx) return null;
        try { audio.ctx = new Ctx(); } catch (e) { return null; }
        return audio.ctx;
    }

    // Dubai-style police wail: a bright, brassy two-voice tone sweeping
    // linearly between ~560 Hz and ~1500 Hz and back, once every ~2.2 s.
    // A triangle LFO gives the straight-line rise and fall of a real siren
    // (a sine LFO lingers at the top and bottom and sounds softer).
    var SWEEP_CENTER = 1030;   // Hz, middle of the sweep
    var SWEEP_DEPTH = 470;     // Hz, ± from the centre
    var SWEEP_RATE = 0.45;     // Hz  (1 / 2.2 s per full up-down cycle)

    function build(ctx) {
        var t = ctx.currentTime;

        var master = ctx.createGain();
        master.gain.setValueAtTime(0.0001, t);
        master.gain.exponentialRampToValueAtTime(0.32, t + 0.15);

        // Bandpass-ish brightness: lowpass with resonance gives the horn bite.
        var filter = ctx.createBiquadFilter();
        filter.type = 'lowpass';
        filter.frequency.value = 2800;
        filter.Q.value = 4;

        var osc1 = ctx.createOscillator();   // main horn
        osc1.type = 'sawtooth';
        osc1.frequency.value = SWEEP_CENTER;

        var osc2 = ctx.createOscillator();   // square, slightly detuned: body
        osc2.type = 'square';
        osc2.frequency.value = SWEEP_CENTER;
        osc2.detune.value = 9;

        var osc2Gain = ctx.createGain();
        osc2Gain.gain.value = 0.45;

        // Triangle LFO sweeps both voices together, linear up and down.
        var lfo = ctx.createOscillator();
        lfo.type = 'triangle';
        lfo.frequency.value = SWEEP_RATE;
        var lfoGain = ctx.createGain();
        lfoGain.gain.value = SWEEP_DEPTH;
        lfo.connect(lfoGain);
        lfoGain.connect(osc1.frequency);
        lfoGain.connect(osc2.frequency);

        osc1.connect(filter);
        osc2.connect(osc2Gain);
        osc2Gain.connect(filter);
        filter.connect(master);
        master.connect(ctx.destination);

        osc1.start(t);
        osc2.start(t);
        lfo.start(t);
        audio.siren = { osc1: osc1, osc2: osc2, lfo: lfo, master: master };
    }

    function start() {
        audio.want = true;
        var ctx = getCtx();
        if (!ctx) return false;
        if (ctx.state !== 'running') {
            try {
                var p = ctx.resume();
                if (p && p.then) {
                    p.then(function () {
                        if (audio.want) start();
                    }).catch(function () { /* still blocked — retry on next poll */ });
                }
            } catch (e) { /* still blocked — retry on next poll */ }
            return false;
        }
        if (!audio.siren) build(ctx);
        return true;
    }

    function stop() {
        audio.want = false;
        if (!audio.siren) return;
        var ctx = audio.ctx;
        var n = audio.siren;
        audio.siren = null;
        try {
            n.master.gain.cancelScheduledValues(ctx.currentTime);
            n.master.gain.setValueAtTime(
                Math.max(0.0001, n.master.gain.value || 0.2), ctx.currentTime
            );
            n.master.gain.exponentialRampToValueAtTime(0.0001, ctx.currentTime + 0.25);
        } catch (e) { /* ignore */ }
        setTimeout(function () {
            try { n.osc1.stop(); } catch (e) { /* ignore */ }
            try { n.osc2.stop(); } catch (e) { /* ignore */ }
            try { n.lfo.stop(); } catch (e) { /* ignore */ }
        }, 320);
    }

    function unlock() {
        var ctx = getCtx();
        if (!ctx) return Promise.resolve(false);
        if (ctx.state === 'suspended') {
            try {
                var p = ctx.resume();
                if (p && p.then) {
                    return p
                        .then(function () { return ctx.state === 'running'; })
                        .catch(function () { return false; });
                }
            } catch (e) { /* ignore */ }
            return Promise.resolve(false);
        }
        return Promise.resolve(ctx.state === 'running');
    }

    function isRunning() {
        return !!(audio.ctx && audio.ctx.state === 'running' && audio.siren);
    }

    window.AMSTaskerSiren = {
        start: start,
        stop: stop,
        unlock: unlock,
        isRunning: isRunning,
        state: function () { return audio.ctx ? audio.ctx.state : 'unavailable'; },
    };
})();
