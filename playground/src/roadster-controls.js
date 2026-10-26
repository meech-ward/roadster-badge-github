// Development controls call the app's portable set_controls/control_action
// interface. They do not emulate touch pads or claim a physical badge mapping.
const SLUG = 'roadster_sandbox';
const KEY_ACTIONS = {
  KeyW: 'forward', ArrowUp: 'forward', KeyS: 'reverse', ArrowDown: 'reverse',
  KeyA: 'left', ArrowLeft: 'left', KeyD: 'right', ArrowRight: 'right', Space: 'brake',
  Digit1: 'nozzle-0', Digit2: 'nozzle-1', Digit3: 'nozzle-2', Digit4: 'nozzle-3',
  Digit5: 'nozzle-4', Digit6: 'nozzle-5', Digit7: 'nozzle-6', Digit8: 'nozzle-7',
  Digit9: 'nozzle-8', Digit0: 'nozzle-9',
  KeyB: 'rear', KeyJ: 'lift', KeyQ: 'lift-left', KeyE: 'lift-right',
};
const GROUPS = { rear: [0, 1, 2, 3], lift: [4, 5, 6, 7], 'lift-left': [4, 6], 'lift-right': [5, 7] };
const SHORTCUTS = {
  KeyH: ['trick', 'hop'], KeyK: ['trick', 'kickflip'], KeyL: ['trick', 'spin'],
  KeyI: ['trick', 'backflip'],
  KeyX: ['cancel_trick'], KeyT: ['refill'], KeyR: ['reset'],
};

function isEditing(target) {
  return target instanceof Element && Boolean(target.closest('input, textarea, select, [contenteditable]:not([contenteditable="false"])'));
}

export function createRoadsterControls(api, reportError) {
  const panel = document.querySelector('#roadster-controls');
  const generic = document.querySelector('#badge-controls');
  const fanButton = document.querySelector('#roadster-fan');
  const held = new Map();
  const keysAwaitingRelease = new Set();
  let active = false;
  let fan = false;
  let lastStatus = null;

  function renderStatus() {
    if (!active) return;
    const needsRelease = [...keysAwaitingRelease].map(key => key.replace(/^Key/, '').replace(/^Digit/, ''));
    const message = needsRelease.length
      ? 'Release and press ' + needsRelease.join(', ') + ' again to resume those controls.'
      : lastStatus?.message || 'Ready. Hold J for lift, or try a preset.';
    const messageNode = document.querySelector('#roadster-control-message');
    if (messageNode.textContent !== message) messageNode.textContent = message;
    const metrics = [];
    if (Number.isFinite(lastStatus?.air_percent)) metrics.push('Air ' + Math.floor(lastStatus.air_percent) + '%');
    if (Number.isFinite(lastStatus?.lift_ratio)) metrics.push('Lift ' + lastStatus.lift_ratio.toFixed(1) + '× weight');
    if (lastStatus?.trick) metrics.push(lastStatus.trick + (lastStatus.phase ? ' · ' + lastStatus.phase : ''));
    if (lastStatus?.parked_count) metrics.push(lastStatus.parked_count + ' parked Teslas');
    const metricsNode = document.querySelector('#roadster-control-metrics');
    const text = metrics.join(' · ');
    if (metricsNode.textContent !== text) metricsNode.textContent = text;
    if (lastStatus?.scene) document.querySelector('#roadster-scene').value = lastStatus.scene;
    if (lastStatus?.camera) document.querySelector('#roadster-camera').value = lastStatus.camera;
    const reduced = lastStatus?.effects === 'reduced';
    const effectsButton = document.querySelector('#roadster-effects');
    effectsButton.setAttribute('aria-pressed', String(reduced));
    effectsButton.querySelector('[data-effects-state]').textContent = reduced ? 'Reduced' : 'Full';
    if (Number.isFinite(lastStatus?.speed_mph)) {
      const state = lastStatus.acceleration_state;
      let timer = state === 'waiting' ? 'stop to arm' : 'ready';
      if (state === 'invalid') timer = lastStatus.acceleration_reason || 'run interrupted';
      else if (Number.isFinite(lastStatus.acceleration_time)) {
        timer = lastStatus.acceleration_time.toFixed(2) + ' s' + (state === 'running' ? '…' : '');
      }
      document.querySelector('#roadster-speed-readout').textContent =
        Math.round(lastStatus.speed_mph) + ' mph · Rear jets ' +
        (lastStatus.rear_force / 1000).toFixed(1) + ' kN · 0–60 mph ' + timer;
    }
    for (const button of panel.querySelectorAll('[data-trick]')) {
      button.setAttribute('aria-pressed', String(lastStatus?.trick === button.dataset.trick));
    }
  }

  function publish() {
    if (!active) return;
    const actions = new Set(held.values());
    const nozzles = Array(10).fill(0);
    for (const action of actions) {
      if (action.startsWith('nozzle-')) nozzles[Number(action.slice(7))] = 1;
      for (const index of GROUPS[action] ?? []) nozzles[index] = 1;
    }
    api.setAppControls({
      throttle: Number(actions.has('forward')) - Number(actions.has('reverse')),
      brake: Number(actions.has('brake')),
      steer: Number(actions.has('left')) - Number(actions.has('right')),
      fan,
      nozzles,
    });
    fanButton.setAttribute('aria-pressed', String(fan));
    fanButton.querySelector('[data-fan-state]').textContent = fan ? 'On' : 'Off';
    for (const button of panel.querySelectorAll('[data-control]')) {
      const action = button.dataset.control;
      const on = action.startsWith('nozzle-') ? Boolean(nozzles[Number(action.slice(7))]) : actions.has(action);
      button.setAttribute('aria-pressed', String(on));
    }
  }

  function clear(resetFan = false) {
    for (const key of held.keys()) if (key.startsWith('key-')) keysAwaitingRelease.add(key.slice(4));
    held.clear();
    if (resetFan) fan = false;
    publish();
    renderStatus();
  }

  function toggleFan() { fan = !fan; publish(); }

  async function action(name, value = null) {
    try { await api.appAction(name, value); }
    catch (error) { reportError(error); }
  }

  for (const button of panel.querySelectorAll('[data-control]')) {
    button.addEventListener('pointerdown', event => {
      if (!active || event.button !== 0) return;
      event.preventDefault();
      button.setPointerCapture(event.pointerId);
      held.set('pointer-' + event.pointerId, button.dataset.control);
      publish();
    });
    const release = event => { held.delete('pointer-' + event.pointerId); publish(); };
    button.addEventListener('pointerup', release);
    button.addEventListener('pointercancel', release);
    button.addEventListener('lostpointercapture', release);
  }
  fanButton.addEventListener('click', toggleFan);
  document.querySelector('#roadster-effects').addEventListener('click', () =>
    action('effects', lastStatus?.effects === 'reduced' ? 'full' : 'reduced'));
  document.querySelector('#roadster-reset').addEventListener('click', () => action('reset'));
  document.querySelector('#roadster-refill').addEventListener('click', () => action('refill'));
  document.querySelector('#roadster-cancel-trick').addEventListener('click', () => action('cancel_trick'));
  const vehicleSelect = document.querySelector('#roadster-vehicle');
  vehicleSelect.addEventListener('change', () => {
    document.querySelector('#roadster-visit').disabled = vehicleSelect.value === 'roadster';
    vehicleSelect.blur();
  });
  document.querySelector('#roadster-inspect').addEventListener('click', () => action('inspect', vehicleSelect.value));
  document.querySelector('#roadster-visit').addEventListener('click', () => action('visit', vehicleSelect.value));
  document.querySelector('#roadster-camera').addEventListener('change', event => {
    event.target.blur();
    action('camera', event.target.value);
  });
  for (const button of panel.querySelectorAll('[data-trick]')) {
    button.addEventListener('click', () => action('trick', button.dataset.trick));
  }
  document.querySelector('#roadster-scene').addEventListener('change', event => {
    // Return keyboard control to driving after choosing a scene.
    event.target.blur();
    action('scene', event.target.value);
  });

  addEventListener('keydown', event => {
    if (!active || isEditing(event.target) || event.metaKey || event.ctrlKey || event.altKey) return;
    const actionName = KEY_ACTIONS[event.code];
    if (actionName) {
      event.preventDefault();
      // A reset, reboot or focus loss releases inputs. A key still physically
      // down must be released before its repeating events can drive again.
      if (event.repeat && !held.has('key-' + event.code)) return;
      keysAwaitingRelease.delete(event.code);
      held.set('key-' + event.code, actionName);
      publish();
      renderStatus();
    } else if (event.code === 'KeyF' || SHORTCUTS[event.code]) {
      event.preventDefault();
      if (event.repeat) return;
      if (event.code === 'KeyF') toggleFan();
      else action(...SHORTCUTS[event.code]);
    }
  });
  addEventListener('keyup', event => {
    // Always release a tracked key, even if focus moved into an input meanwhile.
    if (!held.has('key-' + event.code) && !keysAwaitingRelease.has(event.code)) return;
    keysAwaitingRelease.delete(event.code);
    held.delete('key-' + event.code);
    event.preventDefault();
    publish();
    renderStatus();
  });

  return {
    clear,
    status(data) {
      lastStatus = data;
      renderStatus();
    },
    beforeAction(action, value) {
      if (!active) return;
      if (action === 'reset' || action === 'scene' || action === 'visit') clear(true);
      if (action === 'inspect') clear();
      if (action === 'scene' && ['collection', 'ramp', 'flat'].includes(value)) {
        document.querySelector('#roadster-scene').value = value;
      }
    },
    afterAction(action, result) {
      if (!active || !result || typeof result.fan_on !== 'boolean') return;
      // Presets never own the fan toggle. Keep the live input intact even
      // when an older action acknowledgement arrives after a newer F press.
      lastStatus = result;
      renderStatus();
    },
    activate(slug) {
      active = slug === SLUG;
      document.body.classList.toggle('roadster-mode', active);
      document.querySelector('h1').textContent = active ? 'Roadster sandbox' : 'Badge Lab';
      document.querySelector('#developer-tools').open = !active;
      panel.hidden = !active;
      generic.hidden = active;
      held.clear();
      fan = false;
      lastStatus = null;
      document.querySelector('#roadster-scene').value = 'collection';
      document.querySelector('#roadster-camera').value = 'chase';
      publish();
      renderStatus();
    },
  };
}
