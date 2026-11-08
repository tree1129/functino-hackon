import { ConsoleClient } from '../api/console-client.js';
import { CameraWindowManager } from '../features/robot/camera-windows.js';
import { RecordingBar, formatDuration } from '../features/recording/recording-bar.js';

const client = new ConsoleClient();
const $ = (id) => document.getElementById(id);
let capabilities;
let viewerBase = '';
let shownViewerUrl = '';
let liveCameraMode = true;
let knownRecordings = [];
let activeRecording = null;

function bytes(value) {
  const amount = Number(value) || 0;
  if (amount < 1024) return `${amount} B`;
  if (amount < 1024 ** 2) return `${(amount / 1024).toFixed(1)} KiB`;
  if (amount < 1024 ** 3) return `${(amount / 1024 ** 2).toFixed(1)} MiB`;
  return `${(amount / 1024 ** 3).toFixed(2)} GiB`;
}

function setNotice(message, error = false) {
  $('recordingNotice').textContent = message;
  $('recordingNotice').classList.toggle('error', error);
}

function renderSources(document) {
  const byId = new Map(document.sources.map((source) => [source.id, source]));
  const titles = { robot: '机器人', camera: '相机', sensor: '传感器', environment: '环境与任务' };
  $('recordingSources').innerHTML = Object.entries(document.groups).map(([group, sources]) => `
    <fieldset><legend>${titles[group]}</legend>${sources.map((id) => {
      const source = byId.get(id);
      const checked = document.default_sources.includes(id) ? ' checked' : '';
      return `<label><input type="checkbox" name="recordingSource" value="${id}"${checked}> ${source?.label || id}</label>`;
    }).join('')}</fieldset>`).join('');
  $('recordingRoot').textContent = document.recording_root;
}

function renderStatus(status) {
  const states = { INACTIVE: '未启用', ARMED: '已就绪', RECORDING: '录制中', PAUSED: '已暂停', FINALIZING: '正在保存', CANCELLING: '正在取消' };
  $('recordingState').textContent = states[status.state] || status.state;
  $('recordingDuration').textContent = formatDuration(status.duration_seconds);
  $('recordingSize').textContent = bytes(status.size_bytes);
  $('recordingHealth').textContent = Object.keys(status.errors || {}).length ? `${Object.keys(status.errors).length} 项异常` : '正常';
  $('recordingHealth').classList.toggle('error', Boolean(Object.keys(status.errors || {}).length));
  $('btnArmRecording').disabled = status.state !== 'INACTIVE' && status.state !== 'ARMED';
  $('recordingConfig').disabled = status.state !== 'INACTIVE' && status.state !== 'ARMED';
}

function showViewer(dataUrls, forceReload = false) {
  const urls = (Array.isArray(dataUrls) ? dataUrls : [dataUrls]).filter(Boolean);
  const viewerKey = urls.join('\n');
  if (!viewerBase || !urls.length || (!forceReload && shownViewerUrl === viewerKey)) return;
  shownViewerUrl = viewerKey;
  const query = new URLSearchParams();
  urls.forEach((url) => query.append('url', url));
  $('rerunViewer').src = `${viewerBase}/?${query}`;
  $('viewerEmpty').hidden = true;
  $('rerunViewer').hidden = false;
}

async function importRecording(file) {
  if (!file) return;
  if (!file.name.toLowerCase().endsWith('.rrd')) return setNotice('请选择 Rerun .rrd 文件。', true);
  try {
    const existing = knownRecordings.find((item) => item.name === file.name && Number(item.size_bytes) === file.size);
    if (existing) {
      playRecording(existing);
      setNotice(`已打开现有记录 ${existing.name}`);
      return;
    }
    setNotice(`正在载入 ${file.name}…`);
    const item = await client.importRecording(file);
    await refreshHistory();
    playRecording(item);
  } catch (error) { setNotice(`载入失败：${error.message}`, true); }
  finally { $('recordingFileInput').value = ''; }
}

async function arm() {
  const sources = [...document.querySelectorAll('[name="recordingSource"]:checked')].map((item) => item.value);
  if (!sources.length) return setNotice('至少选择一个数据源。', true);
  try {
    const status = await client.armRecording({
      sources,
      representation: $('recordingRepresentation').value,
      directory: $('recordingDirectory').value,
      filename: $('recordingFilename').value,
      sample_hz: Number($('recordingSampleHz').value),
      camera_fps: Number($('recordingCameraFps').value),
    });
    renderStatus(status);
    setNotice('录制已启用。现在可以在任意页面的顶栏开始录制。');
    window.consoleRecordingBar?.refresh();
  } catch (error) { setNotice(error.message, true); }
}

async function playRecording(item) {
  activeRecording = item;
  const id = item.id.split('/').map(encodeURIComponent).join('/');
  const query = new URLSearchParams({ token: item.playback_token, representation: $('playbackRepresentation').value });
  const playbackUrl = `${location.origin}/api/playback-files/${id}.rrd?${query}`;
  setNotice(`正在准备 ${item.name}…`);
  try {
    await client.request(`/api/recordings/${id}/prepare-playback?representation=${encodeURIComponent($('playbackRepresentation').value)}`);
    showViewer(playbackUrl, true);
    $('viewerTitle').textContent = item.name;
    setNotice(`正在播放 ${item.name}`);
  } catch (error) {
    setNotice(`准备回放失败：${error.message}`, true);
  }
}

async function refreshHistory(autoPlay = false) {
  try {
    const { recordings } = await client.getRecordings();
    knownRecordings = recordings;
    $('recordingList').innerHTML = recordings.length ? recordings.map((item) => `
      <article class="recording-item" data-id="${item.id}">
        <div><strong>${item.name}</strong><span>${formatDuration(item.duration_seconds || 0)} · ${bytes(item.size_bytes)} · ${item.model || '未知机型'}</span></div>
        <div><button data-play type="button">播放</button><a href="/api/recordings/${item.id.split('/').map(encodeURIComponent).join('/')}/file">下载</a><button data-delete class="danger" type="button">删除</button></div>
      </article>`).join('') : '<p class="empty-list">还没有已完成的记录。</p>';
    $('recordingList').querySelectorAll('.recording-item').forEach((element) => {
      const item = recordings.find((entry) => entry.id === element.dataset.id);
      element.querySelector('[data-play]').onclick = () => playRecording(item);
      element.querySelector('[data-delete]').onclick = async () => {
        if (!confirm(`删除 ${item.name}？此操作不可恢复。`)) return;
        try { await client.deleteRecording(item.id); await refreshHistory(); }
        catch (error) { setNotice(error.message, true); }
      };
    });
    if (autoPlay && recordings.length && !shownViewerUrl) playRecording(recordings[0]);
  } catch (error) { setNotice(`读取记录失败：${error.message}`, true); }
}

const cameraManager = new CameraWindowManager({
  container: $('cameraWindows'), select: $('cameraSourceSelect'), addButton: $('btnAddCameraWindow'), count: $('cameraWindowCount'),
  isRobotWorkspace: () => liveCameraMode, showEmpty: true, draggable: true,
});

async function boot() {
  await window.consoleAuthReady;
  try {
    const [recordingCapabilities, viewer] = await Promise.all([client.getRecordingCapabilities(), client.request('/api/recording/viewer')]);
    capabilities = recordingCapabilities;
    renderSources(capabilities);
    viewerBase = `http://${location.hostname}:${viewer.port}`;
    if (location.protocol === 'https:') setNotice('当前 Rerun Viewer 使用独立 HTTP 端口；HTTPS 部署需要在反向代理中转发该端口。', true);
    $('btnArmRecording').onclick = arm;
    $('btnRefreshRecordings').onclick = refreshHistory;
    $('btnToggleRecordingSidebar').onclick = () => {
      const collapsed = $('consoleApp').classList.toggle('recording-sidebar-collapsed');
      $('btnToggleRecordingSidebar').setAttribute('aria-expanded', String(!collapsed));
      $('btnToggleRecordingSidebar').textContent = collapsed ? '›' : '‹';
      $('btnToggleRecordingSidebar').title = collapsed ? '展开录制栏' : '收起录制栏';
    };
    $('btnToggleCameraSidebar').onclick = () => {
      const collapsed = $('consoleApp').classList.toggle('camera-collapsed');
      $('btnToggleCameraSidebar').setAttribute('aria-expanded', String(!collapsed));
      $('btnToggleCameraSidebar').textContent = collapsed ? '‹' : '›';
      $('btnToggleCameraSidebar').title = collapsed ? '展开相机栏' : '收起相机栏';
    };
    const openFile = () => $('recordingFileInput').click();
    $('btnOpenRecording').onclick = openFile;
    $('btnOpenRecordingEmpty').onclick = openFile;
    $('recordingFileInput').onchange = (event) => importRecording(event.target.files?.[0]);
    $('btnLiveRecordingView').onclick = () => {
      liveCameraMode = true;
      cameraManager.setWorkspaceActive(true);
      $('rerunViewer').hidden = true;
      $('viewerEmpty').hidden = false;
      $('viewerTitle').textContent = '实时相机监看';
      setNotice('实时相机已切换到右侧监看区，请添加需要查看的相机窗口。');
      client.getRecordingStatus().then(renderStatus);
    };
    $('playbackRepresentation').onchange = () => { if (activeRecording) playRecording(activeRecording); };
    cameraManager.setWorkspaceActive(true);
    window.consoleRecordingBar = new RecordingBar({ client, onStatus: renderStatus });
    await refreshHistory();
    renderStatus(await client.getRecordingStatus());
  } catch (error) { setNotice(`初始化失败：${error.message}`, true); }
}

boot();
