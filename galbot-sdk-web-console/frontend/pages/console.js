import { ConsoleClient } from '../api/console-client.js';
import { CameraWindowManager } from '../features/robot/camera-windows.js?v=20260911-2';
import { RobotConsoleController } from '../features/robot/console-controller.js';
import { MotionWorkspaceController } from '../features/motion/controller.js';
import { NavigationWorkspaceController } from '../features/navigation/workspace-controller.js';
import { SharedSceneRuntime } from '../scene/shared-scene-runtime.js';
import { loadScenePreferences, saveScenePreferences } from '../scene/scene-preferences.js';

const byId = (id) => document.getElementById(id);
const client = new ConsoleClient();
const runtime = new SharedSceneRuntime({ client, canvas: byId('mapCanvas'), status: renderStatus });
const { scene, navigationStore, robotStore, robot } = runtime;
const scenePreferences = loadScenePreferences();
let capabilities = null;
let activeWorkspace = 'robot';

function setPill(id, text, ok = null) {
  const element = byId(id);
  element.textContent = text;
  if (ok !== null) {
    element.classList.toggle('ok', ok);
    element.classList.toggle('bad', !ok);
  }
}

function renderStatus(message, error = false) {
  const element = byId('sceneStatus');
  element.textContent = message;
  element.classList.toggle('error', error);
}

function setContextPanelCollapsed(collapsed) {
  const app = byId('consoleApp');
  const button = byId('btnTogglePanel');
  app.classList.toggle('panel-collapsed', collapsed);
  button.textContent = collapsed ? '展开详情' : '收起详情';
  button.title = collapsed ? '展开当前工作区侧栏' : '收起当前工作区侧栏';
  button.setAttribute('aria-expanded', String(!collapsed));
  requestAnimationFrame(() => scene.fit());
}

function makeConsoleSectionsCollapsible() {
  document.querySelectorAll('.workspace-panel').forEach((panel) => {
    Array.from(panel.children).forEach((section) => {
      if (section.tagName !== 'SECTION' || section.dataset.foldReady) return;
      const heading = section.querySelector(':scope > h2, :scope > .section-head h2');
      if (!heading) return;
      const details = document.createElement('details');
      details.className = ['console-fold', ...Array.from(section.classList).filter((name) => name !== 'console-section')].join(' ');
      // Keep only the controls needed for the current workflow visible. The
      // remaining diagnostics and display tuning stay available, but no
      // longer turn the side panel into one long ungrouped form.
      details.open = section.classList.contains('motion-simple-only')
        || section.classList.contains('nav-simple-only')
        || section.classList.contains('robot-scene-settings');
      details.hidden = section.hidden;
      const summary = document.createElement('summary');
      summary.textContent = heading.textContent;
      details.append(summary);
      while (section.firstChild) {
        const child = section.firstChild;
        if (child === heading || child.matches?.('.section-head')) {
          if (child.matches?.('.section-head')) {
            const button = child.querySelector('button');
            if (button) summary.append(button);
          }
          section.removeChild(child);
        } else details.append(child);
      }
      details.dataset.foldReady = 'true';
      section.replaceWith(details);
    });
  });
}

const motionPanel = document.querySelector('[data-panel="motion"]');
const motion = new MotionWorkspaceController({ client, runtime, scene, panelRoot: motionPanel, renderStatus });
const navigationPanel = document.querySelector('[data-panel="navigation"]');
const navigation = new NavigationWorkspaceController({ client, scene, panelRoot: navigationPanel, status: renderStatus });
const robotPanel = document.querySelector('[data-panel="robot"]');
const robotWorkspace = new RobotConsoleController({
  client, runtime, scene, robot, robotStore, navigationStore,
  panelRoot: robotPanel,
  jointDrawer: byId('jointDrawer'),
  sensorOverlay: byId('robotSensorOverlay'),
  toggleJoints: byId('btnToggleJoints'),
  scenePreferences,
  renderStatus,
});
const cameraWindows = new CameraWindowManager({
  container: byId('cameraWindows'),
  select: byId('cameraSourceSelect'),
  addButton: byId('btnAddCameraWindow'),
  count: byId('cameraWindowCount'),
  isRobotWorkspace: () => activeWorkspace === 'robot',
});
// The shell owns one SceneMapView. Profiles will grow as the dedicated full
// workspaces are migrated here; the host prevents page-local interactions
// from leaking between Robot, Motion, and Navigation.
runtime.registerWorkspace('robot', {
  activate: () => robotWorkspace.activate(),
  deactivate: () => robotWorkspace.deactivate(),
  dispose: () => robotWorkspace.dispose(),
});
runtime.registerWorkspace('motion', {
  activate: () => motion.activate(),
  deactivate: () => motion.deactivate(),
  dispose: () => motion.dispose(),
});
runtime.registerWorkspace('navigation', {
  activate: () => navigation.activate(),
  deactivate: () => navigation.deactivate(),
  dispose: () => navigation.dispose(),
});

function shouldSyncSceneJoints() {
  if (activeWorkspace === 'motion' && motion.isPreviewing()) return false;
  return robotWorkspace.shouldSyncSceneJoints();
}

const workspaceMeta = {
  robot: { name: 'Robot', summary: '状态、关节与共享场景', eyebrow: 'ROBOT / LIVE MODEL', title: '机器人状态' },
  motion: { name: 'Motion', summary: '规划、碰撞与障碍物', eyebrow: 'MOTION / PLAN', title: '运动调试' },
  navigation: { name: 'Navigation', summary: '定位、地图与任务', eyebrow: 'NAVIGATION / MAP', title: '导航任务' },
};

function switchWorkspace(name) {
  const meta = workspaceMeta[name];
  if (!meta) return;
  runtime.activateWorkspace(name);
  activeWorkspace = name;
  // Preview mode belongs only to the Robot workspace. Other workspaces always
  // begin from the latest live joint state.
  if (name !== 'robot') runtime.render();
  document.querySelectorAll('.workspace-link').forEach((button) => button.classList.toggle('active', button.dataset.workspace === name));
  document.querySelectorAll('.workspace-panel').forEach((panel) => panel.classList.toggle('active', panel.dataset.panel === name));
  byId('workspaceName').textContent = meta.name;
  byId('workspaceSummary').textContent = meta.summary;
  byId('sceneEyebrow').textContent = meta.eyebrow;
  byId('sceneTitle').textContent = meta.title;
  robotWorkspace.updateOverlayVisibility();
  cameraWindows.setWorkspaceActive(name === 'robot');
  const stopLabels = { robot: '⚠ 急停', motion: '■ 停止控制器', navigation: '■ 停止导航' };
  const stopButton = byId('btnStop');
  stopButton.textContent = stopLabels[name] || 'STOP';
  stopButton.title = stopLabels[name] || 'STOP';
  const resumeButton = byId('btnResumeEmergency');
  resumeButton.hidden = name !== 'robot';
  resumeButton.style.display = name === 'robot' ? 'inline-flex' : 'none';
  history.replaceState(null, '', `${location.pathname}${location.search}#${name}`);
  requestAnimationFrame(() => scene.fit());
}

function renderNavigation(state) {
  setPill('connPill', state.connected ? 'SDK 已连接' : 'SDK 未初始化', state.connected);
  robotWorkspace.render(robotStore.state, state);
  navigation.render(state);
}

async function initialize() {
  // Apply workspace chrome immediately so Robot safety controls are visible
  // even while SDK/model initialization is still in progress.
  switchWorkspace(location.hash.slice(1) || 'robot');
  makeConsoleSectionsCollapsible();
  document.querySelectorAll('.workspace-link[data-workspace]').forEach((button) => button.addEventListener('click', () => switchWorkspace(button.dataset.workspace)));
  byId('btnTogglePanel').onclick = () => setContextPanelCollapsed(!byId('consoleApp').classList.contains('panel-collapsed'));
  byId('btnResumeEmergency').onclick = async () => {
    if (!window.confirm('确认机器人已解除物理急停并且周围环境安全，现在恢复软件急停？')) return;
    try {
      const result = await client.command('robot', 'resume_emergency_stop', {});
      renderStatus(result.dry_run ? '恢复急停：脱机预览' : '已发送恢复急停请求');
    } catch (error) { renderStatus(`恢复急停失败：${error.message}`, true); }
  };
  const setView = (mode) => {
    scene.setViewMode(mode);
    byId('btnView3d').classList.toggle('active', mode === '3d');
    byId('btnViewTop').classList.toggle('active', mode === 'top');
    byId('scenePointerHint').textContent = mode === 'top'
      ? '左键或右键平移 · Shift + 左键平面旋转 · 滚轮缩放'
      : '左键旋转 · Shift + 左键或右键平移 · 滚轮缩放';
    saveScenePreferences({ viewMode: mode });
  };
  byId('btnView3d').onclick = () => setView('3d');
  byId('btnViewTop').onclick = () => setView('top');
  setView(scenePreferences.viewMode === 'top' ? 'top' : '3d');
  const focusRobotView = () => {
    if (scene.viewMode !== '3d') setView('3d');
    const focused = scene.focusRobot();
    if (!focused) scene.fitToBounds();
    scene.fit();
    saveScenePreferences({ viewMode: '3d' });
    return true;
  };
  scene.onFocusRobot = focusRobotView;
  const navigationPathLayer = byId('visNavigationPath');
  navigationPathLayer.checked = scenePreferences.layers.path !== false;
  navigationPathLayer.onchange = (event) => {
    scene.setLayerVisible('path', event.target.checked);
    saveScenePreferences({ layers: { path: event.target.checked } });
  };
  const navigationPathWidth = byId('navigationPathWidth');
  navigationPathWidth.value = String(scene.pathWidthPx || 4);
  byId('navigationPathWidthValue').textContent = `${navigationPathWidth.value} px`;
  navigationPathWidth.oninput = (event) => {
    scene.setPathWidth(Number(event.target.value));
    byId('navigationPathWidthValue').textContent = `${event.target.value} px`;
  };
  robotWorkspace.bind();
  motion.bind();
  navigation.bind();
  byId('btnStop').onclick = async () => {
    const workspace = location.hash.slice(1) || 'robot';
    try {
      const result = workspace === 'robot'
        ? await client.command('robot', 'emergency_stop', {})
        : workspace === 'motion'
          ? await client.command('motion', 'stop_controller', { group: 'all' })
          : await client.stop();
      renderStatus(result.dry_run ? `${workspace} STOP：脱机模式` : `${workspace} STOP 已提交`);
    } catch (error) { renderStatus(`${workspace} STOP 失败：${error.message}`, true); }
  };
  robotStore.addEventListener('change', (event) => robotWorkspace.render(event.detail, navigationStore.state));
  navigationStore.addEventListener('change', (event) => renderNavigation(event.detail));


  try {
    const result = await runtime.start({ getRenderOptions: () => ({ joints: shouldSyncSceneJoints() }), onEvent: (event) => {
      if (event.type === 'connection') setPill('connPill', 'event connection closed', false);
      if (event.type === 'service_error') renderStatus(`SDK event sampling failed: ${event.data?.message || 'unknown error'}`, true);
    }, onError: (error) => setPill('connPill', 'event connection failed', false) });
    capabilities = result.capabilities;
    if (!capabilities) return;
    setPill('modePill', capabilities.live_mode ? '实机' : '脱机', true);
    setPill('modelPill', (capabilities.model || 'unknown').toUpperCase());
    robotWorkspace.buildJointPreview();
    renderStatus('共享机器人场景已就绪');
  } catch (error) {
    renderStatus(`初始化失败：${error.message}`, true);
  }

  switchWorkspace(location.hash.slice(1) || 'robot');
}

window.consoleAuthReady.then(() => initialize());
