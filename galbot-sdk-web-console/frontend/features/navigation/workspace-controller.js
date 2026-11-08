import { quaternionToYaw, sceneGoalToPose, yawToQuaternion } from '../../domain/pose.js';

export class NavigationWorkspaceController {
  constructor({ client, scene, panelRoot, status }) {
    this.client = client;
    this.scene = scene;
    this.panelRoot = panelRoot;
    this.status = status;
    this.bound = false;
    this.active = false;
    this.requestEpoch = 0;
    this.pathRequestEpoch = 0;
    this.renderedPath = undefined;
    this.latestState = null;
    this.pathCache = new Map();
    this.routePicking = false;
    this.routeWaypoints = [];
    this.listenerAbort = null;
  }

  $(id) {
    const element = this.panelRoot.querySelector(`#${id}`);
    if (!element) throw new Error(`Navigation workspace element #${id} is missing`);
    return element;
  }

  bind() {
    if (this.bound) return;
    this.bound = true;
    this.listenerAbort = new AbortController();
    this.$('btnNavigate').onclick = () => this.sendGoal(this.goalPose());
    this.$('btnNavPickGoal').onclick = () => this.startScenePick();
    this.$('btnNavCheckReachability').onclick = () => this.checkReachability();
    this.$('btnNavStartWaypoints').onclick = () => this.toggleWaypointPicking();
    this.$('btnNavUndoWaypoint').onclick = () => this.undoWaypoint();
    this.$('btnNavClearWaypoints').onclick = () => this.clearWaypoints();
    this.$('btnNavSubmitWaypoints').onclick = () => this.navigateWaypoints();
    this.$('navMultiPointMode').onchange = () => this.updateMultiPointMode();
    this.$('navigationPathTask').onchange = () => this.selectPathTask();
    this.updateMultiPointMode();
    this.renderWaypoints();
  }

  activate() {
    this.active = true;
    this.scene.onGoalChange = (goal) => this.onSceneGoalChange(goal);
    this.scene.onGoalCommit = (goal) => this.onSceneGoalCommit(goal);
    void this.refreshTaskHistory();
  }

  deactivate() {
    this.active = false;
    this.requestEpoch += 1;
    this.scene.onGoalChange = () => {};
    this.scene.onGoalCommit = () => {};
    this.stopScenePicking();
    this.clearWaypoints();
    if (this.scene.mode !== 'pan') this.scene.setMode('pan');
  }

  dispose() {
    this.deactivate();
    this.listenerAbort?.abort();
    this.listenerAbort = null;
    for (const id of [
      'btnNavigate', 'btnNavPickGoal', 'btnNavCheckReachability', 'btnNavStartWaypoints',
      'btnNavUndoWaypoint', 'btnNavClearWaypoints', 'btnNavSubmitWaypoints', 'navMultiPointMode',
      'navigationPathTask',
    ]) {
      const element = this.$(id);
      element.onclick = null;
      element.onchange = null;
    }
    this.bound = false;
  }

  pose(prefix) {
    const yaw = Number(this.$(`${prefix}Yaw`).value) || 0;
    return {
      x: Number(this.$(`${prefix}X`).value) || 0,
      y: Number(this.$(`${prefix}Y`).value) || 0,
      z: 0,
      ...yawToQuaternion(yaw),
      frame_id: 'map',
    };
  }

  goalPose() { return this.pose('nav'); }

  startScenePick() {
    if (!this.active) return;
    this.stopScenePicking();
    this.$('btnNavPickGoal').classList.add('is-picking');
    this.$('btnNavPickGoal').setAttribute('aria-pressed', 'true');
    this.scene.setMode('goal');
    this.status('在场景中点击并拖动朝向以设置导航目标');
  }

  toggleWaypointPicking() {
    if (!this.active) return;
    if (this.routePicking) {
      this.stopScenePicking();
      this.status(`已结束选点，共 ${this.routeWaypoints.length} 个导航点`);
      return;
    }
    this.$('btnNavPickGoal').classList.remove('is-picking');
    this.$('btnNavPickGoal').setAttribute('aria-pressed', 'false');
    this.routePicking = true;
    const button = this.$('btnNavStartWaypoints');
    button.classList.add('is-picking');
    button.setAttribute('aria-pressed', 'true');
    button.textContent = '■ 结束选点';
    this.scene.setMode('goal');
    this.status(`依次选择导航点；当前已有 ${this.routeWaypoints.length} 个`);
  }

  stopScenePicking() {
    this.routePicking = false;
    for (const id of ['btnNavPickGoal', 'btnNavStartWaypoints']) {
      const button = this.panelRoot.querySelector(`#${id}`);
      button?.classList.remove('is-picking');
      button?.setAttribute('aria-pressed', 'false');
    }
    const routeButton = this.panelRoot.querySelector('#btnNavStartWaypoints');
    if (routeButton) routeButton.textContent = '＋ 开始依次选点';
    if (this.scene.mode !== 'pan') this.scene.setMode('pan');
  }

  onSceneGoalChange(goal) {
    if (!goal || !this.active || this.routePicking) return;
    this.$('navX').value = Number(goal.x).toFixed(3);
    this.$('navY').value = Number(goal.y).toFixed(3);
    this.$('navYaw').value = Number(goal.yaw || 0).toFixed(3);
  }

  onSceneGoalCommit(goal) {
    if (!goal || !this.active) return;
    const pose = sceneGoalToPose(goal, 'map');
    if (this.routePicking) {
      this.routeWaypoints.push(pose);
      this.scene.setNavigationWaypoints(this.routeWaypoints);
      this.scene.setGoal(null);
      this.scene.setMode('goal');
      this.renderWaypoints();
      this.status(`已选择第 ${this.routeWaypoints.length} 个导航点，请继续选择或下发`);
      return;
    }
    this.stopScenePicking();
    this.$('navX').value = Number(pose.x).toFixed(3);
    this.$('navY').value = Number(pose.y).toFixed(3);
    this.$('navYaw').value = quaternionToYaw(pose).toFixed(3);
    this.status('目标已设置，请确认参数后点击“下发导航目标”');
  }

  async run(label, action) {
    const epoch = ++this.requestEpoch;
    try {
      const result = await action();
      if (!this.active || epoch !== this.requestEpoch) return null;
      this.$('navigationCommandResult').textContent = JSON.stringify(result, null, 2);
      this.status(result.dry_run ? `${label}：脱机预览` : `${label}已提交`, !result.accepted);
      if (result.task_id) await this.refreshTaskHistory();
      return result;
    } catch (error) {
      if (this.active && epoch === this.requestEpoch) {
        this.$('navigationCommandResult').textContent = `${label}失败：${error.message}`;
        this.status(`${label}失败：${error.message}`, true);
      }
      return null;
    }
  }

  sendGoal(pose) {
    this.scene.setGoal({ x: pose.x, y: pose.y, yaw: quaternionToYaw(pose) });
    return this.run('目标导航', () => this.client.command('navigation', 'navigate_to_goal_v2', {
      ...pose,
      frame_id: 'map',
      max_velocity: ['navMaxVx', 'navMaxVy', 'navMaxVyaw'].map((id) => Number(this.$(id).value)),
      collision_check: this.$('navCollisionCheck').checked,
      timeout: Number(this.$('navTimeout').value),
      omni_plan: this.$('navOmniPlan').checked,
    }));
  }

  checkReachability() {
    const pose = this.goalPose();
    const goal = [pose.x, pose.y, pose.z, pose.qx, pose.qy, pose.qz, pose.qw];
    return this.run('路径可达性检查', () => this.client.command('navigation', 'check_path_reachability', { goal }));
  }

  undoWaypoint() {
    if (!this.routeWaypoints.length) return;
    this.routeWaypoints.pop();
    this.scene.setNavigationWaypoints(this.routeWaypoints);
    this.renderWaypoints();
  }

  clearWaypoints() {
    this.routeWaypoints = [];
    this.scene.setNavigationWaypoints([]);
    if (this.bound) this.renderWaypoints();
  }

  renderWaypoints() {
    const count = this.routeWaypoints.length;
    const trajectory = this.$('navMultiPointMode').value === 'navigate_along_trajectory';
    this.$('btnNavUndoWaypoint').disabled = count === 0;
    this.$('btnNavClearWaypoints').disabled = count === 0;
    this.$('btnNavSubmitWaypoints').disabled = count < 2;
    this.$('btnNavSubmitWaypoints').textContent = trajectory ? `下发 ${count} 点轨迹` : `下发 ${count} 个途经点`;
    const list = this.$('navWaypointList');
    list.replaceChildren();
    if (!count) {
      const empty = document.createElement('li');
      empty.textContent = '尚未选择导航点';
      list.append(empty);
      return;
    }
    this.routeWaypoints.forEach((pose) => {
      const item = document.createElement('li');
      item.textContent = `x ${pose.x.toFixed(3)} · y ${pose.y.toFixed(3)} · yaw ${quaternionToYaw(pose).toFixed(3)}`;
      list.append(item);
    });
  }

  updateMultiPointMode() {
    const trajectory = this.$('navMultiPointMode').value === 'navigate_along_trajectory';
    this.$('navTrajectoryOptions').hidden = !trajectory;
    this.$('navMultiPointModeHint').textContent = trajectory
      ? '机器人沿所选点组成的连续轨迹行驶。'
      : '机器人依次到达每个途经点。';
    this.renderWaypoints();
  }

  async navigateWaypoints() {
    if (this.routeWaypoints.length < 2) return window.alert('请至少选择两个导航点。');
    if (!window.confirm(`确认按顺序下发 ${this.routeWaypoints.length} 个导航点？`)) return;
    const poses = this.routeWaypoints.map((pose) => [pose.x, pose.y, pose.z, pose.qx, pose.qy, pose.qz, pose.qw]);
    const trajectory = this.$('navMultiPointMode').value === 'navigate_along_trajectory';
    this.stopScenePicking();
    const shared = { poses, frame_id: 'map', collision_check: this.$('navCollisionCheck').checked, confirmed: true };
    const result = trajectory
      ? await this.run('轨迹导航', () => this.client.command('navigation', 'navigate_along_trajectory', {
        ...shared, speed_ratio: Number(this.$('navTrajectorySpeedRatio').value),
      }))
      : await this.run('途经点导航', () => this.client.command('navigation', 'navigate_through_waypoints', shared));
    if (result) this.clearWaypoints();
  }

  async refreshTaskHistory() {
    try {
      const response = await this.client.getNavigationTasks();
      const select = this.$('navigationPathTask');
      const selected = select.value;
      const tasks = Array.isArray(response.tasks) ? response.tasks : [];
      select.replaceChildren(new Option('当前任务（自动跟随）', ''));
      const modeLabels = { goal: '目标导航', waypoints: '途经点', trajectory: '轨迹导航', velocity: '速度导航' };
      tasks.forEach((task) => {
        const created = task.created_ns ? new Date(task.created_ns / 1e6).toLocaleTimeString() : '';
        const shortId = String(task.task_id).slice(-8);
        const label = `${modeLabels[task.mode] || '导航'} · ${created || '未知时间'} · ${shortId}`;
        select.append(new Option(label, task.task_id));
      });
      select.value = tasks.some((task) => task.task_id === selected) ? selected : '';
      if (selected && !select.value) this.selectPathTask();
    } catch (error) {
      if (this.active) this.$('navigationPathHint').textContent = `导航历史读取失败：${error.message}`;
    }
  }

  async selectPathTask() {
    const epoch = ++this.pathRequestEpoch;
    const taskId = this.$('navigationPathTask').value;
    if (!taskId) {
      this.renderSelectedPath(this.latestState?.path, this.latestState?.task_id, false);
      return;
    }
    const cached = this.pathCache.get(taskId);
    if (cached) this.renderSelectedPath(cached, taskId, true);
    this.$('navigationPathHint').textContent = `正在读取任务 ${taskId} 的规划路径…`;
    try {
      const path = await this.client.getNavigationPath(taskId);
      if (!this.active || epoch !== this.pathRequestEpoch || this.$('navigationPathTask').value !== taskId) return;
      this.pathCache.set(taskId, path);
      this.renderSelectedPath(path, taskId, true);
    } catch (error) {
      if (this.active && epoch === this.pathRequestEpoch) {
        this.scene.setPath([]);
        this.renderedPath = null;
        this.$('navigationPathHint').textContent = `任务路径读取失败：${error.message}`;
      }
    }
  }

  renderSelectedPath(path, taskId, explicitSelection) {
    const usable = path?.task_id && (!taskId || path.task_id === taskId) ? path : null;
    if (usable !== this.renderedPath) {
      this.scene.setPath(usable?.available ? usable.poses : []);
      this.renderedPath = usable;
    }
    const prefix = explicitSelection ? '所选任务' : '当前任务';
    if (usable?.available) this.$('navigationPathHint').textContent = `${prefix}路径：${usable.poses.length} 个点，坐标系 ${usable.frame_id || 'map'}。`;
    else if (taskId) this.$('navigationPathHint').textContent = usable?.msg || `${prefix}尚未发布规划路径。`;
    else this.$('navigationPathHint').textContent = '等待导航任务；路径将在规划器发布后显示。';
  }

  render(state) {
    this.latestState = state;
    this.$('navigationStatus').textContent = JSON.stringify(state, null, 2);
    if (state.path?.task_id) this.pathCache.set(state.path.task_id, state.path);
    const selectedTaskId = this.$('navigationPathTask').value;
    if (!selectedTaskId) {
      this.renderSelectedPath(state.path, state.task_id, false);
    } else if (state.path?.task_id === selectedTaskId) {
      this.renderSelectedPath(state.path, selectedTaskId, true);
    }
  }
}
