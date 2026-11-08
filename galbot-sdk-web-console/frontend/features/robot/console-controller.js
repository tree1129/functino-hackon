import { JointPreviewController } from './joint-preview-controller.js';
import { saveScenePreferences } from '../../scene/scene-preferences.js';

const POSE_KEYS = ['x', 'y', 'z', 'qx', 'qy', 'qz', 'qw'];
const JOYSTICK_DEADZONE = 0.04;
const JOYSTICK_EXPO = 0.75;

function shapeJoystickMagnitude(value) {
  if (value <= JOYSTICK_DEADZONE) return 0;
  const normalized = Math.min(1, (value - JOYSTICK_DEADZONE) / (1 - JOYSTICK_DEADZONE));
  return (1 - JOYSTICK_EXPO) * normalized + JOYSTICK_EXPO * normalized ** 2;
}

export class RobotConsoleController {
  constructor({ client, runtime, scene, robot, robotStore, navigationStore, panelRoot, jointDrawer, sensorOverlay, toggleJoints, scenePreferences, renderStatus }) {
    this.client = client;
    this.runtime = runtime;
    this.scene = scene;
    this.robot = robot;
    this.robotStore = robotStore;
    this.navigationStore = navigationStore;
    this.panelRoot = panelRoot;
    this.jointDrawer = jointDrawer;
    this.sensorOverlay = sensorOverlay;
    this.toggleJoints = toggleJoints;
    this.scenePreferences = scenePreferences;
    this.renderStatus = renderStatus;
    this.bound = false;
    this.active = false;
    this.liveJointSync = true;
    this.renderedJointState = null;
    this.lidarConfigTimer = null;
    this.overlayVisibility = { ...(scenePreferences.robotOverlays || {}) };
    this.listenerAbort = null;
    this.joystick = { pointerId: null, timer: null, vx: 0, vy: 0, wz: 0 };
    this.jointPreview = new JointPreviewController({
      scene,
      container: this.drawer$('jointControls'),
      renderStatus,
    });
  }

  $(id) {
    const element = this.panelRoot.querySelector(`#${id}`);
    if (!element) throw new Error(`Robot workspace element #${id} is missing`);
    return element;
  }

  drawer$(id) {
    const element = this.jointDrawer.querySelector(`#${id}`);
    if (!element) throw new Error(`Robot joint drawer element #${id} is missing`);
    return element;
  }

  on(element, type, listener) {
    element.addEventListener(type, listener, { signal: this.listenerAbort.signal });
  }

  bind() {
    if (this.bound) return;
    this.bound = true;
    this.listenerAbort = new AbortController();

    this.on(this.toggleJoints, 'click', () => this.setJointDrawerCollapsed(!this.jointDrawer.classList.contains('collapsed')));
    this.on(this.drawer$('syncRobotJoints'), 'change', (event) => {
      this.liveJointSync = event.target.checked;
      if (this.liveJointSync) this.syncJointPreviewToRobot();
      this.renderStatus(this.liveJointSync ? '已开启真机关节角实时同步' : '已暂停真机关节角同步，可自由调整模型预览');
    });
    this.on(this.drawer$('jointControls'), 'input', () => {
      this.liveJointSync = false;
      this.drawer$('syncRobotJoints').checked = false;
    });
    this.on(this.$('robotPreviewGroup'), 'change', (event) => this.jointPreview.setSelectedGroup(event.target.value));
    this.on(this.$('btnRobotSyncJoints'), 'click', () => {
      this.syncJointPreviewToRobot();
      this.liveJointSync = false;
      this.drawer$('syncRobotJoints').checked = false;
      this.renderStatus('已同步关节状态；当前可独立调整预览');
    });
    this.on(this.$('btnRobotResetJoints'), 'click', () => {
      this.liveJointSync = false;
      this.drawer$('syncRobotJoints').checked = false;
      this.jointPreview.reset();
    });
    this.on(this.$('btnRobotApplyJoints'), 'click', () => this.applyJointPreview());
    this.on(this.$('btnRobotSetGripper'), 'click', () => this.setGripper());
    this.bindJoystick();
    this.bindSceneControls();
    this.bindLocalAssets();
  }

  activate() {
    this.active = true;
    this.updateOverlayVisibility();
    this.render(this.robotStore.state, this.navigationStore.state);
  }

  deactivate() {
    this.active = false;
    this.updateOverlayVisibility();
    this.stopJoystick(true);
    this.liveJointSync = true;
    this.drawer$('syncRobotJoints').checked = true;
    this.syncJointPreviewToRobot();
    this.setJointDrawerCollapsed(true);
  }

  dispose() {
    this.deactivate();
    clearTimeout(this.lidarConfigTimer);
    this.lidarConfigTimer = null;
    this.listenerAbort?.abort();
    this.listenerAbort = null;
    this.bound = false;
  }

  shouldSyncSceneJoints() {
    return !this.active || this.liveJointSync;
  }

  buildJointPreview() {
    this.$('robotPreviewGroup').value = 'head';
    this.jointPreview.setSelectedGroup('head');
    if (this.robotStore.state.jointState) this.syncJointPreviewToRobot();
    else this.jointPreview.build();
  }

  setJointDrawerCollapsed(collapsed) {
    const workspace = this.jointDrawer.closest('.scene-workspace');
    this.jointDrawer.classList.toggle('collapsed', collapsed);
    workspace?.classList.toggle('joint-drawer-open', !collapsed);
    this.toggleJoints.textContent = collapsed ? '展开关节' : '收起关节';
    this.toggleJoints.title = collapsed ? '展开关节预览' : '收起关节预览';
    this.toggleJoints.setAttribute('aria-expanded', String(!collapsed));
  }

  syncJointPreviewToRobot() {
    if (!this.robotStore.state.jointState) return;
    this.jointPreview.syncFromJointState(this.robotStore.state.jointState);
    this.renderedJointState = this.robotStore.state.jointState;
    this.runtime.render();
  }

  async applyJointPreview() {
    const { names: joint_names, position: positions } = this.jointPreview.getState();
    const max_speed = Number(this.$('robotJointSpeed').value);
    const timeout = Number(this.$('robotJointTimeout').value);
    if (!this.robotStore.state.jointState) return window.alert('请先等待真机关节状态同步完成。');
    if (!joint_names.length || !Number.isFinite(max_speed) || !Number.isFinite(timeout)) return window.alert('关节预览或控制参数无效。');
    if (!window.confirm(`确认下发 ${joint_names.length} 个关节的当前预览姿态？请确认机器人周围安全。`)) return;
    try {
      const result = await this.client.command('robot', 'set_joint_positions', { joint_names, positions, max_speed, timeout });
      this.renderStatus(result.dry_run ? '脱机预览：未下发关节指令' : '关节指令已提交');
    } catch (error) {
      this.renderStatus(`关节控制失败：${error.message}`, true);
    }
  }

  async setGripper() {
    const side = this.$('robotGripperSide').value;
    if (!window.confirm(`确认设置${side === 'left' ? '左' : '右'}夹爪？请确认周围安全。`)) return;
    try {
      const result = await this.client.command('robot', 'set_gripper_command', {
        side,
        width: Number(this.$('robotGripperWidth').value),
        speed: Number(this.$('robotGripperSpeed').value),
        effort: Number(this.$('robotGripperEffort').value),
      });
      this.renderStatus(result.dry_run ? '脱机预览：未下发夹爪指令' : '夹爪指令已提交');
    } catch (error) {
      this.renderStatus(`夹爪控制失败：${error.message}`, true);
    }
  }

  bindJoystick() {
    const pad = this.$('baseJoystick');
    const knob = this.$('baseJoystickKnob');
    const send = () => this.client.command('robot', 'base_velocity', {
      linear: [this.joystick.vx, this.joystick.vy, 0],
      angular: [0, 0, this.joystick.wz], duration: 0.0, confirmed: true,
    }).catch((error) => this.renderStatus(`底盘控制失败：${error.message}`, true));
    const update = (event) => {
      const rect = pad.getBoundingClientRect();
      const max = rect.width * 0.36;
      const rawX = event.clientX - (rect.left + rect.width / 2);
      const rawY = event.clientY - (rect.top + rect.height / 2);
      const rawRadius = Math.hypot(rawX, rawY);
      const clampScale = rawRadius > max ? max / rawRadius : 1;
      const x = rawX * clampScale;
      const y = rawY * clampScale;
      knob.style.transform = `translate(calc(-50% + ${x}px), calc(-50% + ${y}px))`;
      const magnitude = Math.hypot(x, y) / max;
      const speed = shapeJoystickMagnitude(magnitude) * 0.75;
      this.joystick.vx = magnitude ? -y / (magnitude * max) * speed : 0;
      this.joystick.vy = magnitude ? -x / (magnitude * max) * speed : 0;
      this.renderJoystick();
    };
    const startTimer = () => {
      clearInterval(this.joystick.timer);
      this.joystick.timer = setInterval(send, 100);
      send();
    };
    this.on(pad, 'pointerdown', (event) => {
      this.joystick.pointerId = event.pointerId;
      pad.setPointerCapture(event.pointerId);
      update(event);
      startTimer();
    });
    this.on(pad, 'pointermove', (event) => {
      if (event.pointerId === this.joystick.pointerId) update(event);
    });
    this.on(pad, 'pointerup', () => this.stopJoystick(true));
    this.on(pad, 'pointercancel', () => this.stopJoystick(true));
    this.on(window, 'blur', () => this.stopJoystick(true));
    const rotate = (value) => {
      this.joystick.wz = value;
      this.renderJoystick();
      startTimer();
    };
    this.on(this.$('joystickRotateLeft'), 'pointerdown', () => rotate(1.0));
    this.on(this.$('joystickRotateRight'), 'pointerdown', () => rotate(-1.0));
    this.on(this.$('joystickRotateLeft'), 'pointerup', () => this.stopJoystick(true));
    this.on(this.$('joystickRotateRight'), 'pointerup', () => this.stopJoystick(true));
    this.on(this.$('btnJoystickStop'), 'click', async () => {
      try {
        await this.client.command('robot', 'emergency_stop', {});
        this.stopJoystick(false);
      } catch (error) {
        this.renderStatus(`急停失败：${error.message}`, true);
      }
    });
  }

  stopJoystick(sendZero = false) {
    const wasMoving = this.joystick.timer !== null || this.joystick.vx !== 0 || this.joystick.vy !== 0 || this.joystick.wz !== 0;
    clearInterval(this.joystick.timer);
    Object.assign(this.joystick, { pointerId: null, timer: null, vx: 0, vy: 0, wz: 0 });
    const knob = this.panelRoot.querySelector('#baseJoystickKnob');
    if (knob) knob.style.transform = 'translate(-50%, -50%)';
    this.renderJoystick();
    if (sendZero && wasMoving) {
      this.client.command('robot', 'base_velocity', {
        linear: [0, 0, 0], angular: [0, 0, 0], duration: 0.0, confirmed: true,
      }).catch((error) => this.renderStatus(`底盘停止失败：${error.message}`, true));
    }
  }

  renderJoystick() {
    for (const [id, value] of [['joystickVx', this.joystick.vx], ['joystickVy', this.joystick.vy], ['joystickWz', this.joystick.wz]]) {
      const output = this.panelRoot.querySelector(`#${id}`);
      if (output) output.textContent = value.toFixed(2);
    }
  }

  bindSceneControls() {
    const bindLayer = (id, layer) => {
      const element = this.$(id);
      element.checked = this.scenePreferences.layers[layer] !== false;
      this.on(element, 'change', (event) => {
        this.scene.setLayerVisible(layer, event.target.checked);
        saveScenePreferences({ layers: { [layer]: event.target.checked } });
      });
    };
    bindLayer('visStaticMap', 'pcd');
    bindLayer('visLiveLidar', 'localCloud');
    const grid = this.$('visGroundGrid');
    grid.checked = this.scenePreferences.layers.grid !== false;
    this.on(grid, 'change', (event) => {
      this.scene.setGroundGridVisible(event.target.checked);
      saveScenePreferences({ layers: { grid: event.target.checked } });
    });
    this.on(this.$('visRobotModel'), 'change', (event) => this.scene.setRobotVisible(event.target.checked));
    this.on(this.$('visMapFrame'), 'change', (event) => this.scene.setCoordinateFrameVisible('map', event.target.checked));
    this.on(this.$('visBaseFrame'), 'change', (event) => this.scene.setCoordinateFrameVisible('base', event.target.checked));
    const overlays = {
      visPowerStatus: 'power',
      visRobotPose: 'pose',
      visJointLeg: 'leg',
      visJointHead: 'head',
      visJointLeftArm: 'left_arm',
      visJointRightArm: 'right_arm',
    };
    for (const [id, key] of Object.entries(overlays)) {
      const input = this.$(id);
      input.checked = this.overlayVisibility[key] === true;
      this.on(input, 'change', (event) => {
        this.overlayVisibility[key] = event.target.checked;
        saveScenePreferences({ robotOverlays: { [key]: event.target.checked } });
        this.updateOverlayVisibility();
      });
    }

    const displayMode = this.$('sharedRobotDisplayMode');
    displayMode.value = this.scenePreferences.robotDisplayMode === 'collision' ? 'collision' : 'visual';
    this.on(displayMode, 'change', async (event) => {
      saveScenePreferences({ robotDisplayMode: event.target.value });
      this.robot.setDisplayMode(event.target.value);
      try { await this.robot.loadModel(); }
      catch (error) { this.renderStatus(`机器人模型加载失败：${error.message}`, true); }
    });
    this.bindPointSize('sharedStaticPointSize', 'sharedStaticPointSizeValue', 'staticPointSize', 'staticPointSizePx', 2);
    this.bindPointSize('sharedLidarPointSize', 'sharedLidarPointSizeValue', 'lidarPointSize', 'localPointSizePx', 3);
    ['staticCropEnabled', 'staticCropX', 'staticCropY', 'staticCropZ'].forEach((id) => this.on(this.$(id), 'change', () => this.updateStaticMapCrop()));
    ['visLiveLidar', 'lidarMaxPoints', 'lidarCropEnabled', 'lidarCropX', 'lidarCropY', 'lidarCropZ'].forEach((id) => this.on(this.$(id), 'change', () => this.updateLidarDisplay()));
    this.on(this.$('btnReloadStaticMap'), 'click', () => this.restoreStaticMap());
  }

  bindPointSize(id, valueId, preferenceKey, sceneKey, fallback) {
    const input = this.$(id);
    input.value = String(this.scenePreferences[preferenceKey] || fallback);
    this.$(valueId).textContent = `${input.value} px`;
    this.on(input, 'input', (event) => {
      const size = Number(event.target.value);
      this.$(valueId).textContent = `${size} px`;
      this.scene.setCloudViewOptions({ [sceneKey]: size });
      saveScenePreferences({ [preferenceKey]: size });
    });
  }

  boundedNumber(id, minimum, maximum, fallback) {
    const input = this.$(id);
    const value = Number(input.value);
    const bounded = Math.min(maximum, Math.max(minimum, Number.isFinite(value) ? value : fallback));
    input.value = String(bounded);
    return bounded;
  }

  updateStaticMapCrop() {
    this.scene.setStaticCloudViewOptions({
      crop: this.$('staticCropEnabled').checked ? {
        halfX: this.boundedNumber('staticCropX', 0.1, 1000, 10),
        halfY: this.boundedNumber('staticCropY', 0.1, 1000, 10),
        halfZ: this.boundedNumber('staticCropZ', 0.1, 1000, 3),
      } : null,
    });
  }

  updateLidarDisplay() {
    const payload = {
      enabled: this.$('visLiveLidar').checked,
      max_points: Math.round(this.boundedNumber('lidarMaxPoints', 1000, 15000, 8000)),
      crop: this.$('lidarCropEnabled').checked ? {
        half_x: this.boundedNumber('lidarCropX', 0.1, 50, 5),
        half_y: this.boundedNumber('lidarCropY', 0.1, 50, 5),
        half_z: this.boundedNumber('lidarCropZ', 0.1, 20, 2),
      } : null,
    };
    this.scene.setCloudViewOptions({ localMaxPoints: payload.max_points, localCrop: null });
    clearTimeout(this.lidarConfigTimer);
    this.lidarConfigTimer = setTimeout(() => {
      this.client.command('robot', 'configure_lidar_stream', payload)
        .catch((error) => this.renderStatus(`实时雷达配置失败：${error.message}`, true));
    }, 180);
  }

  bindLocalAssets() {
    this.on(this.$('robotLocalPcdFile'), 'change', async (event) => {
      const file = event.target.files?.[0];
      if (!file) return;
      try {
        const count = await this.robot.loadLocalPcd(file);
        this.$('robotLocalAssetStatus').textContent = `本地 PCD：${file.name} · ${count.toLocaleString()} 点`;
      } catch (error) {
        this.renderStatus(`本地 PCD 加载失败：${error.message}`, true);
      } finally {
        event.target.value = '';
      }
    });
    this.on(this.$('btnRobotRestoreStaticMap'), 'click', () => this.restoreStaticMap());
    this.on(this.$('robotLocalUrdfFile'), 'change', async (event) => {
      const file = event.target.files?.[0];
      if (!file) return;
      try {
        await this.robot.loadLocalUrdf(file);
        if (this.robotStore.state.jointState) this.syncJointPreviewToRobot();
        else this.jointPreview.build();
        this.$('robotLocalAssetStatus').textContent = `本地 URDF：${file.name}`;
      } catch (error) {
        this.renderStatus(`本地 URDF 加载失败：${error.message}`, true);
      } finally {
        event.target.value = '';
      }
    });
    this.on(this.$('btnRobotRestoreModel'), 'click', async () => {
      try {
        await this.robot.loadModel();
        if (this.robotStore.state.jointState) this.syncJointPreviewToRobot();
        else this.jointPreview.build();
        this.$('robotLocalAssetStatus').textContent = '已恢复型号默认 URDF';
      } catch (error) {
        this.renderStatus(`默认 URDF 恢复失败：${error.message}`, true);
      }
    });
  }

  async restoreStaticMap() {
    try {
      const loaded = await this.robot.loadStaticGlobalCloud();
      this.$('robotLocalAssetStatus').textContent = loaded ? '已恢复服务端静态地图' : '服务端静态地图不可用';
    } catch (error) {
      this.renderStatus(`静态地图点云加载失败：${error.message}`, true);
    }
  }

  render(state, navigationState) {
    const connected = !!navigationState?.connected;
    const localized = !!navigationState?.localized;
    this.$('robotSdkState').textContent = connected ? '已连接' : '未初始化';
    this.$('robotLocalState').textContent = localized ? '已定位' : '未定位';
    this.$('robotSdkMetric').classList.toggle('state-ok', connected);
    this.$('robotSdkMetric').classList.toggle('state-bad', !connected);
    this.$('robotLocalMetric').classList.toggle('state-ok', localized);
    this.$('robotLocalMetric').classList.toggle('state-bad', !localized);
    const pose = navigationState?.pose;
    this.sensorOverlay.querySelector('#sceneRobotPoseReadout output').textContent = pose
      ? `[${POSE_KEYS.map((key) => Number.isFinite(Number(pose[key])) ? Number(pose[key]).toFixed(3) : '--').join(', ')}]`
      : '暂无数据';
    const joints = state.jointState || [];
    const positions = new Map(joints.map((joint) => [joint.name, Number(joint.position)]));
    for (const [id, prefix, count] of [['sceneJointLegReadout', 'leg', 5], ['sceneJointHeadReadout', 'head', 2], ['sceneJointLeftArmReadout', 'left_arm', 7], ['sceneJointRightArmReadout', 'right_arm', 7]]) {
      this.sensorOverlay.querySelector(`#${id} output`).textContent = state.jointState
        ? `[${Array.from({ length: count }, (_, index) => positions.get(`${prefix}_joint${index + 1}`)).map((value) => Number.isFinite(value) ? value.toFixed(3) : '--').join(', ')}]`
        : '暂无数据';
    }
    this.renderSensors(state);
    if (this.liveJointSync && state.jointState && state.jointState !== this.renderedJointState) {
      this.jointPreview.syncFromJointState(state.jointState);
      this.renderedJointState = state.jointState;
    }
  }

  renderSensors(state) {
    const sensors = state.sensors || {};
    const bms = sensors.bms || {};
    const charging = bms.charging_status;
    const number = (value, unit) => Number.isFinite(Number(value)) ? `${Number(value).toFixed(1)} ${unit}` : '--';
    this.sensorOverlay.querySelector('#scenePowerElectrical').textContent = `${number(bms.voltage, 'V')} / ${number(bms.current, 'A')}`;
    this.sensorOverlay.querySelector('#scenePowerTemperature').textContent = number(bms.temperature, '°C');
    this.sensorOverlay.querySelector('#scenePowerCharging').textContent = charging === true ? '正在充电' : charging === false ? '未充电' : charging == null ? '状态未知' : String(charging);
  }

  updateOverlayVisibility() {
    const groups = {
      power: 'scenePowerReadout',
      pose: 'sceneRobotPoseReadout',
      leg: 'sceneJointLegReadout',
      head: 'sceneJointHeadReadout',
      left_arm: 'sceneJointLeftArmReadout',
      right_arm: 'sceneJointRightArmReadout',
    };
    for (const [key, id] of Object.entries(groups)) {
      this.sensorOverlay.querySelector(`#${id}`).hidden = !this.overlayVisibility[key];
    }
    this.sensorOverlay.hidden = !this.active || !Object.values(this.overlayVisibility).some(Boolean);
  }
}
