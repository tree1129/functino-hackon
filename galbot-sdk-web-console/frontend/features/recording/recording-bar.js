import { ConsoleClient } from '../../api/console-client.js';

const ACTIVE_STATES = new Set(['ARMED', 'RECORDING', 'PAUSED', 'FINALIZING', 'CANCELLING']);

export class RecordingBar {
  constructor({ client = new ConsoleClient(), onStatus = null } = {}) {
    this.client = client;
    this.onStatus = onStatus;
    this.busy = false;
    this.element = this.create();
    document.body.append(this.element);
    this.timer = setInterval(() => this.refresh(), 1000);
    this.refresh();
  }

  create() {
    const element = document.createElement('aside');
    element.className = 'global-recording-bar';
    element.hidden = true;
    element.innerHTML = `
      <a class="recording-bar-title" href="/frontend/recording.html"><span class="recording-dot"></span><strong>数据录制与回放</strong></a>
      <span class="recording-bar-state">未启用</span>
      <span class="recording-bar-time">00:00</span>
      <button data-action="start" type="button">开始</button>
      <button data-action="pause" type="button">暂停</button>
      <button data-action="resume" type="button">继续</button>
      <button data-action="finish" type="button">结束</button>
      <button data-action="cancel" class="danger" type="button">取消</button>`;
    element.querySelectorAll('[data-action]').forEach((button) => button.addEventListener('click', () => this.action(button.dataset.action)));
    return element;
  }

  async refresh() {
    try {
      const status = await this.client.getRecordingStatus();
      this.render(status);
      this.onStatus?.(status);
      window.dispatchEvent(new CustomEvent('recording-status', { detail: status }));
    } catch (_) {
      this.element.hidden = true;
    }
  }

  render(status) {
    const active = ACTIVE_STATES.has(status.state);
    this.element.hidden = !active;
    this.element.dataset.state = status.state;
    this.element.querySelector('.recording-bar-state').textContent = ({ ARMED: '已就绪', RECORDING: '录制中', PAUSED: '已暂停', FINALIZING: '正在保存', CANCELLING: '正在取消' })[status.state] || status.state;
    this.element.querySelector('.recording-bar-time').textContent = formatDuration(status.duration_seconds || 0);
    for (const button of this.element.querySelectorAll('[data-action]')) {
      button.hidden = !({ ARMED: ['start', 'cancel'], RECORDING: ['pause', 'finish', 'cancel'], PAUSED: ['resume', 'finish', 'cancel'] }[status.state] || []).includes(button.dataset.action);
      button.disabled = this.busy;
    }
  }

  async action(action) {
    if (this.busy) return;
    const confirmed = action === 'finish' || action === 'cancel';
    if (confirmed && !window.confirm(action === 'finish' ? '结束录制并保存文件？' : '取消录制并删除本次临时文件？')) return;
    this.busy = true;
    try { this.render(await this.client.recordingAction(action, confirmed)); }
    catch (error) { window.alert(`录制操作失败：${error.message}`); }
    finally { this.busy = false; await this.refresh(); }
  }

  dispose() { clearInterval(this.timer); this.element.remove(); }
}

export function formatDuration(seconds) {
  const total = Math.max(0, Math.floor(Number(seconds) || 0));
  const hours = Math.floor(total / 3600);
  const minutes = Math.floor(total % 3600 / 60);
  const rest = total % 60;
  return `${hours ? `${String(hours).padStart(2, '0')}:` : ''}${String(minutes).padStart(2, '0')}:${String(rest).padStart(2, '0')}`;
}
