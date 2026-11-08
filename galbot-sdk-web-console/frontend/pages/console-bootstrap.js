// Keep SDK connection state independent of the optional Three.js scene bundle.
// A blocked CDN must not make a live robot look like an offline/unknown one.
const pill = (id, value, ok = null) => {
  const element = document.getElementById(id);
  if (!element) return;
  element.textContent = value;
  if (ok !== null) { element.classList.toggle('ok', ok); element.classList.toggle('bad', !ok); }
};

function showAuthGate(message = '') {
  const gate = document.getElementById('authGate');
  const error = document.getElementById('authError');
  if (gate) gate.hidden = false;
  if (error) error.textContent = message;
}

function unlockConsole() {
  const gate = document.getElementById('authGate');
  const app = document.getElementById('consoleApp');
  if (gate) { gate.hidden = true; gate.style.display = 'none'; }
  if (app) { app.hidden = false; app.style.display = 'grid'; }
}

function validatedReturnTarget() {
  const value = new URLSearchParams(location.search).get('next');
  if (!value) return '';
  try {
    const target = new URL(value, location.origin);
    if (target.origin !== location.origin || !target.pathname.startsWith('/frontend/')) return '';
    return `${target.pathname}${target.search}${target.hash}`;
  } catch (_) { return ''; }
}

function returnAfterAuthentication() {
  const target = validatedReturnTarget();
  if (!target) return false;
  location.replace(target);
  return true;
}

async function authenticate() {
  const statusResponse = await fetch('/api/auth/status', { cache: 'no-store', credentials: 'same-origin' });
  const status = await statusResponse.json();
  if (status.authenticated) {
    if (returnAfterAuthentication()) return;
    unlockConsole();
    return;
  }
  if (document.body.dataset.authEntry === 'console') {
    const next = `${location.pathname}${location.search}${location.hash}`;
    location.replace(`/frontend/console.html?next=${encodeURIComponent(next)}`);
    await new Promise(() => {});
    return;
  }
  showAuthGate();
  const form = document.getElementById('authForm');
  const input = document.getElementById('authCode');
  const submit = document.getElementById('authSubmit');
  await new Promise((resolve) => {
    form.onsubmit = async (event) => {
      event.preventDefault();
      const code = input.value.trim();
      submit.disabled = true;
      showAuthGate('正在建立连接...');
      try {
        const response = await fetch('/api/auth/login', {
          method: 'POST', headers: { 'content-type': 'application/json' },
          credentials: 'same-origin', body: JSON.stringify({ code }),
        });
        if (!response.ok) throw new Error('访问码错误');
        form.onsubmit = null;
        if (returnAfterAuthentication()) return;
        unlockConsole();
        resolve();
      } catch (error) { showAuthGate(error.message); input.select(); }
      finally { submit.disabled = false; }
    };
  });
}

async function boot() {
  await authenticate();
  try {
    const response = await fetch('/api/capabilities', { cache: 'no-store' });
    if (!response.ok) throw new Error(`HTTP ${response.status}`);
    const capabilities = await response.json();
    pill('modePill', capabilities.live_mode ? '实机' : '脱机', true);
    pill('modelPill', (capabilities.model || 'unknown').toUpperCase());
    pill('connPill', capabilities.live_mode ? 'SDK 已连接' : '脱机预览', true);
  } catch (error) {
    pill('connPill', 'SDK 状态读取失败', false);
    const status = document.getElementById('sceneStatus');
    if (status) { status.textContent = `SDK 状态读取失败：${error.message}`; status.classList.add('error'); }
  }
}
window.consoleAuthReady = window.consoleAuthReady || boot();
