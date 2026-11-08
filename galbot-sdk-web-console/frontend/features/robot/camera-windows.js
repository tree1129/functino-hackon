const SOURCES = [['left_rgb', '左臂 RGB'], ['left_depth', '左臂深度（热力图）'], ['right_rgb', '右臂 RGB'], ['right_depth', '右臂深度（热力图）'], ['head_fisheye', '头部左目 RGB'], ['head_right', '头部右目 RGB'], ['surround_front_left', '环视前左'], ['surround_front_right', '环视前右'], ['surround_rear_left', '环视后左'], ['surround_rear_right', '环视后右']];
const SOURCE_IDS = { 1: 'left_rgb', 2: 'left_depth', 3: 'right_rgb', 4: 'right_depth', 5: 'head_fisheye', 6: 'surround_front_left', 7: 'surround_front_right', 8: 'surround_rear_left', 9: 'surround_rear_right', 10: 'head_right' };
const sourceName = (source) => SOURCES.find(([key]) => key === source)?.[1] || source;

export class CameraWindowManager {
  constructor({ container, select, addButton, count, isRobotWorkspace, showEmpty = false, draggable = false }) {
    this.container = container; this.select = select; this.addButton = addButton; this.count = count; this.isRobotWorkspace = isRobotWorkspace; this.showEmpty = showEmpty; this.draggable = draggable;
    this.windows = new Map(); this.nextId = 1; this.workspaceActive = false; this.socket = null; this.socketReady = false; this.activeSources = new Set();
    this.observer = new IntersectionObserver((entries) => { entries.forEach((entry) => { const item = this.windows.get(entry.target.dataset.cameraWindowId); if (item) item.visible = entry.isIntersecting; }); this.refreshSubscriptions(); }, { threshold: .05 });
    SOURCES.forEach(([value, label]) => this.select.add(new Option(label, value)));
    this.addButton.addEventListener('click', () => this.add(this.select.value));
    document.addEventListener('visibilitychange', () => this.refreshSubscriptions()); window.addEventListener('pagehide', () => this.socket?.close()); this.updateCount();
  }
  setWorkspaceActive(active) {
    this.workspaceActive = active;
    // Camera acquisition is strictly opt-in. Closing the socket cancels all
    // server-side subscriptions when the view is hidden, rather than leaving
    // an unavailable camera read in the background while the model is active.
    if (!active && this.socket) {
      this.socket.close();
      this.socket = null;
      this.socketReady = false;
      this.activeSources.clear();
    }
    this.updateVisibility();
    if (active) this.refreshSubscriptions();
  }
  add(source) {
    const id = `camera-${this.nextId++}`, element = document.createElement('article');
    element.className = 'camera-window'; element.dataset.cameraWindowId = id;
    element.innerHTML = `<header><strong>${sourceName(source)}</strong><span class="camera-frame-status">未订阅</span><button type="button" aria-label="关闭相机窗口">×</button></header><div class="camera-frame-body"><div class="camera-empty">未订阅到数据流<br><small>数据：0</small></div><img hidden alt="${sourceName(source)}" /><canvas hidden></canvas></div>`;
    this.container.append(element);
    const item = { id, source, element, image: element.querySelector('img'), canvas: element.querySelector('canvas'), empty: element.querySelector('.camera-empty'), status: element.querySelector('.camera-frame-status'), visible: false };
    element.querySelector('button').onclick = () => this.remove(id); this.windows.set(id, item); this.observer.observe(element); if (this.draggable) this.enableDrag(element); this.updateCount(); this.updateVisibility();
  }
  enableDrag(element) {
    const header = element.querySelector('header');
    header.addEventListener('pointerdown', (event) => {
      if (event.button !== 0 || event.target.closest('button')) return;
      const bounds = element.getBoundingClientRect();
      element.classList.add('is-floating');
      element.style.left = `${bounds.left}px`; element.style.top = `${bounds.top}px`;
      element.style.width = `${bounds.width}px`; element.style.height = `${bounds.height}px`;
      const offsetX = event.clientX - bounds.left, offsetY = event.clientY - bounds.top;
      header.setPointerCapture(event.pointerId);
      const move = (moveEvent) => {
        const left = Math.max(0, Math.min(innerWidth - 120, moveEvent.clientX - offsetX));
        const top = Math.max(0, Math.min(innerHeight - 40, moveEvent.clientY - offsetY));
        element.style.left = `${left}px`; element.style.top = `${top}px`;
      };
      const stop = () => { header.removeEventListener('pointermove', move); header.removeEventListener('pointerup', stop); header.removeEventListener('pointercancel', stop); };
      header.addEventListener('pointermove', move); header.addEventListener('pointerup', stop); header.addEventListener('pointercancel', stop);
    });
    header.addEventListener('dblclick', (event) => {
      if (event.target.closest('button')) return;
      element.classList.remove('is-floating');
      element.style.left = ''; element.style.top = ''; element.style.width = ''; element.style.height = '';
    });
  }
  remove(id) { const item = this.windows.get(id); if (!item) return; this.observer.unobserve(item.element); if (item.image.dataset.objectUrl) URL.revokeObjectURL(item.image.dataset.objectUrl); item.element.remove(); this.windows.delete(id); this.updateCount(); this.updateVisibility(); this.refreshSubscriptions(); }
  updateCount() { const amount = this.windows.size; this.count.textContent = `${amount} 个窗口`; this.container.dataset.windowCount = String(amount); }
  updateVisibility() { this.container.hidden = !this.workspaceActive || (this.windows.size === 0 && !this.showEmpty); }
  desiredSources() { return new Set([...this.windows.values()].filter((item) => item.visible && this.isRobotWorkspace() && !this.container.hidden && document.visibilityState === 'visible').map((item) => item.source)); }
  refreshSubscriptions() {
    const wanted = this.desiredSources(); if (wanted.size && (!this.socket || this.socket.readyState > WebSocket.OPEN)) this.connect(); if (!this.socketReady) return;
    [...this.activeSources].filter((source) => !wanted.has(source)).forEach((source) => { this.socket.send(JSON.stringify({ op: 'unsubscribe', source })); this.activeSources.delete(source); });
    [...wanted].filter((source) => !this.activeSources.has(source)).forEach((source) => { this.socket.send(JSON.stringify({ op: 'subscribe', source, fps: 10 })); this.activeSources.add(source); });
  }
  connect() {
    if (this.socket && this.socket.readyState <= WebSocket.OPEN) return;
    const socket = new WebSocket(`${location.protocol === 'https:' ? 'wss' : 'ws'}://${location.host}/api/robot/camera-stream`);
    this.socket = socket; socket.binaryType = 'arraybuffer';
    socket.onopen = () => { if (this.socket !== socket) return; this.socketReady = true; this.activeSources.clear(); this.refreshSubscriptions(); };
    socket.onmessage = (event) => { if (this.socket !== socket) return; if (typeof event.data === 'string') this.error(JSON.parse(event.data)); else this.frame(event.data); };
    socket.onclose = () => { if (this.socket !== socket) return; this.socket = null; this.socketReady = false; this.activeSources.clear(); if (this.desiredSources().size) setTimeout(() => this.connect(), 800); };
  }
  error(message) { [...this.windows.values()].filter((item) => item.source === message.source).forEach((item) => { item.empty.hidden = false; item.empty.innerHTML = `未订阅到数据流<br><small>${message.detail || '未知错误'}</small>`; item.status.textContent = '无数据'; item.status.classList.add('error'); }); }
  frame(buffer) {
    const view = new DataView(buffer); if (view.byteLength < 26 || String.fromCharCode(...new Uint8Array(buffer, 0, 4)) !== 'GBC1') return;
    const source = SOURCE_IDS[view.getUint8(4)], kind = view.getUint8(5) ? 'depth' : 'rgb'; if (!source) return;
    const frame = { kind, width: view.getUint16(6), height: view.getUint16(8), depthScale: view.getFloat32(18), depthLow: view.getUint16(22), depthHigh: view.getUint16(24), data: new Uint8Array(buffer, 26) };
    [...this.windows.values()].filter((item) => item.source === source && item.visible).forEach((item) => { if (kind === 'depth') this.paintDepth(item, frame); else this.paintImage(item, frame); item.empty.hidden = true; item.status.textContent = kind === 'depth' ? '深度热力图 · 10 FPS' : '实时 RGB · 10 FPS'; item.status.classList.remove('error'); });
  }
  paintImage(item, frame) { item.canvas.hidden = true; item.image.hidden = false; const old = item.image.dataset.objectUrl; if (old) URL.revokeObjectURL(old); const url = URL.createObjectURL(new Blob([frame.data], { type: 'image/jpeg' })); item.image.dataset.objectUrl = url; item.image.src = url; }
  paintDepth(item, frame) { const { width, height, data } = frame; if (!width || !height || data.length < width * height) return; const canvas = item.canvas; canvas.width = width; canvas.height = height; const image = new ImageData(width, height); for (let pixel = 0; pixel < width * height; pixel += 1) { const value = data[pixel], color = value ? heatColor(value / 255) : [20,23,28]; image.data.set([color[0], color[1], color[2], value ? 255 : 0], pixel * 4); } canvas.getContext('2d').putImageData(image, 0, 0); canvas.title = `深度范围约 ${(frame.depthLow / frame.depthScale).toFixed(2)}–${(frame.depthHigh / frame.depthScale).toFixed(2)} m`; item.image.hidden = true; canvas.hidden = false; }
}
function heatColor(value) { const stops = [[35,63,180],[0,188,212],[92,205,99],[253,216,53],[229,57,53]], scaled = value * 4, index = Math.min(3, Math.floor(scaled)), part = scaled - index; return stops[index].map((channel, key) => Math.round(channel + (stops[index + 1][key] - channel) * part)); }
