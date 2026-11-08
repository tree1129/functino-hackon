export class ConsoleClient {
  constructor(base = '') { this.base = base; this.socket = null; }
  async request(path, options = {}) {
    const response = await fetch(`${this.base}${path}`, { headers: { 'content-type': 'application/json' }, ...options });
    const contentType = response.headers.get('content-type') || '';
    const raw = await response.text();
    let body;
    try { body = contentType.includes('json') ? JSON.parse(raw) : JSON.parse(raw); }
    catch (_) { body = { detail: raw || `HTTP ${response.status}` }; }
    if (!response.ok) {
      const detail = body.detail;
      const message = Array.isArray(detail)
        ? detail.map((item) => `${(item.loc || []).slice(1).join('.') || '请求'}：${item.msg || JSON.stringify(item)}`).join('；')
        : typeof detail === 'string' ? detail : JSON.stringify(detail || body);
      throw new Error(message || `HTTP ${response.status}`);
    }
    return body;
  }
  async requestBinary(path, options = {}) {
    const response = await fetch(`${this.base}${path}`, options);
    if (!response.ok) {
      const raw = await response.text();
      let detail = raw;
      try { detail = JSON.parse(raw).detail || raw; } catch (_) { /* use text */ }
      throw new Error(typeof detail === 'string' ? detail : JSON.stringify(detail));
    }
    return { data: new Uint8Array(await response.arrayBuffer()), headers: response.headers, contentType: response.headers.get('content-type') || '' };
  }
  getState() { return this.request('/api/robot/state'); }
  getSceneBootstrap() { return this.request('/api/scene/bootstrap'); }
  getCapabilities() { return this.request('/api/capabilities'); }
  getRecordingCapabilities() { return this.request('/api/recording/capabilities'); }
  getRecordingStatus() { return this.request('/api/recording/status'); }
  armRecording(config) { return this.request('/api/recording/arm', { method: 'POST', body: JSON.stringify(config) }); }
  recordingAction(action, confirmed = false) { return this.request(`/api/recording/${encodeURIComponent(action)}`, { method: 'POST', body: JSON.stringify({ confirmed }) }); }
  getRecordings() { return this.request('/api/recordings'); }
  async importRecording(file) {
    const response = await fetch(`/api/recordings/import?filename=${encodeURIComponent(file.name)}`, { method: 'POST', headers: { 'content-type': 'application/octet-stream' }, body: file });
    const body = await response.json().catch(() => ({ detail: `HTTP ${response.status}` }));
    if (!response.ok) throw new Error(body.detail || `HTTP ${response.status}`);
    return body;
  }
  deleteRecording(id) { return this.request(`/api/recordings/${id.split('/').map(encodeURIComponent).join('/')}`, { method: 'DELETE', body: JSON.stringify({ confirmed: true }) }); }
  navigate(goal) { return this.request('/api/navigation/go', { method: 'POST', body: JSON.stringify(goal) }); }
  getNavigationTasks() { return this.request('/api/navigation/tasks'); }
  getNavigationPath(taskId) { return this.request(`/api/navigation/tasks/${encodeURIComponent(taskId)}/path`); }
  stop() { return this.request('/api/navigation/stop', { method: 'POST', body: '{}' }); }
  relocalize(pose) { return this.request('/api/navigation/relocalize', { method: 'POST', body: JSON.stringify(pose) }); }
  command(service, method, payload = {}) { return this.request(`/api/commands/${encodeURIComponent(service)}/${encodeURIComponent(method)}`, { method: 'POST', body: JSON.stringify(payload) }); }
  subscribe(onEvent, onError = console.error) {
    this.socket?.close();
    this.socket = new WebSocket(`${location.protocol === 'https:' ? 'wss' : 'ws'}://${location.host}/api/events`);
    this.socket.onmessage = (event) => onEvent(JSON.parse(event.data));
    this.socket.onerror = onError;
    this.socket.onclose = () => onEvent({ type: 'connection', data: { connected: false } });
    return () => this.socket?.close();
  }
}
