export class MotionWorkspaceController {
  constructor({ client, runtime, scene, panelRoot = document, renderStatus }) {
    this.client = client;
    this.runtime = runtime;
    this.scene = scene;
    this.panelRoot = panelRoot;
    this.renderStatus = renderStatus;
    this.plan = null;
    this.playbackTimer = null;
    this.playbackIndex = 0;
    this.restoreJointState = null;
    this.bound = false;
    this.active = false;
    this.requestEpoch = 0;
    this.offsetTimer = null;
    this.selectedOffsets = new Map();
    this.offsetExecuting = false;
    this.offsetRequestInFlight = false;
    this.listenerAbort = null;
    this.candidateObstacles = [];
    this.pickedObstaclePoints = [];
    this.selectedCandidateId = null;
    this.selectedServiceObstacleId = null;
    this.serviceObstacles = [];
  }

  bind() {
    if (this.bound) return;
    this.bound = true;
    this.listenerAbort = new AbortController();
    this.$('btnPlanPreview').onclick = () => this.planPreview();
    this.$('btnPlayMotionPlan').onclick = () => this.togglePlayback();
    this.$('btnReplayMotionPlan').onclick = () => this.startPlayback(true);
    this.$('btnRestoreMotionPlan').onclick = () => this.restorePlaybackPose();
    this.$('motionPlaybackScrubber').oninput = (event) => this.showPlaybackFrame(Number(event.target.value));
    this.$('btnExecuteMotionPlan').onclick = () => this.execute();
    this.$('btnMotionSyncPose').onclick = () => this.syncCurrentPose();
    this.$('btnMotionPreviewPose').onclick = () => this.refreshTargetPreview();
    this.$('motionVisualizeTrajectory').onchange = (event) => {
      // Visualization is playback-scoped; checking the option only enables
      // it for the next playback/frame inspection.
      if (!event.target.checked || !this.restoreJointState) this.scene.clearEndEffectorTrajectory?.();
      else this._renderTrajectory();
    };
    for (const id of ['motionX', 'motionY', 'motionZ']) {
      this.$(id).addEventListener('input', () => this.refreshTargetPreview(), { signal: this.listenerAbort.signal });
    }
    this.bindOffsetControls();
    this.bindObstacleEditor();
    this.refreshTargetPreview();
    this.updateObstacleScaleFields();
  }

  $(id) {
    const element = this.panelRoot.querySelector(`#${id}`);
    if (!element) throw new Error(`Motion workspace element #${id} is missing`);
    return element;
  }

  activate() {
    this.active = true;
    this.scene.enableMotionObstacleEditing((obstacle) => this.onObstacleEdited(obstacle));
    this.scene.onPointPickMiss = () => this.renderStatus('请在可见点云上右键选点', true);
    this.renderObstacles();
    this.refreshServiceObstacles();
  }

  deactivate() {
    this.active = false;
    this.requestEpoch += 1;
    this.stopOffset(true);
    this.restorePlaybackPose();
    this.clearPickedObstaclePoints();
    this.scene.setMotionObstacleEditingEnabled?.(false);
    this.scene.setMotionObstacles?.([]);
    this.scene.clearEndEffectorTrajectory?.();
  }

  dispose() {
    this.deactivate();
    this.listenerAbort?.abort();
    this.listenerAbort = null;
    for (const id of [
      'btnPlanPreview', 'btnPlayMotionPlan', 'btnReplayMotionPlan', 'btnRestoreMotionPlan',
      'motionPlaybackScrubber', 'btnExecuteMotionPlan', 'btnSharedMotionPreviewObstacle',
      'btnSharedMotionPickPoints', 'btnSharedMotionClearPoints',
      'btnSharedMotionRefreshService', 'btnSharedMotionRemoveService', 'btnSharedMotionExitEdit',
      'btnSharedMotionLoadCandidate', 'showMotionObstacles',
      'btnMotionSyncPose', 'btnMotionPreviewPose', 'motionVisualizeTrajectory',
    ]) {
      const element = this.$(id);
      element.onclick = null;
      element.oninput = null;
      element.onchange = null;
    }
    this.bound = false;
  }

  isPreviewing() {
    return this.restoreJointState !== null;
  }

  bindOffsetControls() {
    const send = async () => {
      if (!this.offsetExecuting || !this.selectedOffsets.size || !this.active || this.offsetRequestInFlight) return;
      const payload = { arm: this.$('motionChain').value, dx: 0, dy: 0, dz: 0, roll: 0, pitch: 0, yaw: 0, confirmed: true };
      for (const { key, sign } of this.selectedOffsets.values()) {
        payload[key] += sign * (['roll', 'pitch', 'yaw'].includes(key) ? 0.02 : 0.01);
      }
      for (const key of ['dx', 'dy', 'dz', 'roll', 'pitch', 'yaw']) {
        const limit = ['roll', 'pitch', 'yaw'].includes(key) ? 0.02 : 0.01;
        payload[key] = Math.max(-limit, Math.min(limit, payload[key]));
      }
      const activeKeys = Object.entries(payload)
        .filter(([key, value]) => key !== 'arm' && key !== 'confirmed' && value !== 0)
        .map(([key]) => key);
      if (!activeKeys.length) return;
      this.offsetRequestInFlight = true;
      try {
        await this.client.command('motion', 'move_end_effector_offset', payload);
        if (this.offsetExecuting && this.active) this.renderStatus(`末端 ${activeKeys.join(' + ')} 持续偏移中`);
      } catch (error) {
        this.stopOffset();
        if (this.active) this.renderStatus(`末端偏移失败：${error.message}`, true);
      } finally {
        this.offsetRequestInFlight = false;
      }
    };
    this.panelRoot.querySelectorAll('[data-offset]').forEach((button) => {
      const options = { signal: this.listenerAbort.signal };
      button.addEventListener('click', () => {
        if (!this.active || this.offsetExecuting) return;
        const key = button.dataset.offset;
        const sign = Number(button.dataset.value);
        const previous = this.selectedOffsets.get(key);
        if (previous?.sign === sign) this.selectedOffsets.delete(key);
        else this.selectedOffsets.set(key, { key, sign });
        this.renderOffsetSelection();
      }, options);
    });
    const apply = this.$('btnMotionApplyOffset');
    const start = (event) => {
      if (!this.active || !this.selectedOffsets.size || this.offsetExecuting) return;
      event.preventDefault();
      this.offsetExecuting = true;
      apply.classList.add('active');
      apply.setPointerCapture?.(event.pointerId);
      send();
      this.offsetTimer = setInterval(send, 100);
    };
    const stop = () => this.stopOffset();
    const options = { signal: this.listenerAbort.signal };
    apply.addEventListener('pointerdown', start, options);
    apply.addEventListener('pointerup', stop, options);
    apply.addEventListener('pointercancel', stop, options);
    apply.addEventListener('lostpointercapture', stop, options);
    window.addEventListener('blur', stop, options);
    this.renderOffsetSelection();
  }

  renderOffsetSelection() {
    this.panelRoot.querySelectorAll('[data-offset]').forEach((button) => {
      const selected = this.selectedOffsets.get(button.dataset.offset)?.sign === Number(button.dataset.value);
      button.classList.toggle('active', selected);
      button.setAttribute('aria-pressed', String(selected));
    });
    this.$('btnMotionApplyOffset').disabled = !this.selectedOffsets.size;
  }

  stopOffset(clearSelection = false) {
    if (this.offsetTimer) clearInterval(this.offsetTimer);
    this.offsetTimer = null;
    this.offsetExecuting = false;
    this.$('btnMotionApplyOffset').classList.remove('active');
    if (clearSelection) {
      this.selectedOffsets.clear();
      this.renderOffsetSelection();
    }
  }

  refreshTargetPreview() {
    const pose = ['motionX', 'motionY', 'motionZ'].map((id) => this._number(id));
    this.$('motionTargetSummary').textContent = `[${pose.map((item) => item.toFixed(2)).join(', ')}]`;
    return pose;
  }

  async syncCurrentPose() {
    const epoch = this.requestEpoch;
    try {
      const result = await this.client.command('motion', 'end_effector_pose', { arm: this.$('motionChain').value });
      if (!this.active || epoch !== this.requestEpoch) return;
      if (Array.isArray(result.pose) && result.pose.length >= 7) {
        ['motionX', 'motionY', 'motionZ', 'motionQx', 'motionQy', 'motionQz', 'motionQw']
          .forEach((id, index) => { this.$(id).value = result.pose[index]; });
        this.refreshTargetPreview();
      }
      this._setStatus(result.pose ? { message: '已同步真机末端位姿', frame_id: result.frame_id, pose: result.pose } : '当前末端位姿不可用');
    } catch (error) {
      if (this.active && epoch === this.requestEpoch) this._setStatus(`同步末端位姿失败：${error.message}`);
    }
  }

  bindObstacleEditor() {
    this.$('btnSharedMotionPreviewObstacle').onclick = () => this.createCandidatePreview();
    this.$('btnSharedMotionPickPoints').onclick = () => {
      if (!this.active) return;
      if (this.scene.mode === 'motionPointCollection') {
        this.scene.cancelWorldPointCollection();
        this.$('btnSharedMotionPickPoints').textContent = '开始多点选取';
        return;
      }
      this.scene.collectWorldPoints((point) => {
        if (!this.active) return;
        const last = this.pickedObstaclePoints.at(-1);
        if (last && Math.hypot(point.x - last.x, point.y - last.y, point.z - last.z) < 1e-6) return;
        this.pickedObstaclePoints.push(point);
        this.scene.setMotionSelectionPoints?.(this.pickedObstaclePoints);
        this.$('sharedMotionPickedStatus').textContent = `已选 ${this.pickedObstaclePoints.length} 个点；继续右键点云选点，按 Esc 结束`;
      });
      this.$('btnSharedMotionPickPoints').textContent = '取消选点';
    };
    this.$('btnSharedMotionClearPoints').onclick = () => this.clearPickedObstaclePoints();
    this.$('btnSharedMotionRefreshService').onclick = () => this.refreshServiceObstacles();
    this.$('btnSharedMotionRemoveService').onclick = () => this.removeSelectedServiceObstacle();
    this.$('btnSharedMotionExitEdit').onclick = () => {
      this.scene.setMotionObstacleEditingEnabled?.(false);
      this.$('sharedMotionDraftStatus').textContent = '已退出编辑；候选碰撞体仍保留在当前工作区';
    };
    this.$('btnSharedMotionLoadCandidate').onclick = () => this.loadSelectedCandidate();
    this.$('showMotionObstacles').onchange = (event) => this.scene.setMotionObstacleVisible?.(event.target.checked);
    this.$('sharedMotionObstacleType').addEventListener('change', () => {
      this.updateObstacleScaleFields();
      this.updateSelectedCandidateFromForm();
    }, { signal: this.listenerAbort.signal });
    for (const id of [
      'sharedMotionObstacleId', 'sharedMotionObstacleX', 'sharedMotionObstacleY', 'sharedMotionObstacleZ',
      'sharedMotionObstacleSX', 'sharedMotionObstacleSY', 'sharedMotionObstacleSZ',
    ]) this.$(id).addEventListener('input', () => this.updateSelectedCandidateFromForm(), { signal: this.listenerAbort.signal });
  }

  updateObstacleScaleFields() {
    const type = this.$('sharedMotionObstacleType').value;
    const labels = type === 'sphere' ? ['半径', '', ''] : type === 'cylinder' ? ['半径', '高度', ''] : ['X 尺寸', 'Y 尺寸', 'Z 尺寸'];
    ['X', 'Y', 'Z'].forEach((axis, index) => { this.$(`sharedMotionScaleLabel${axis}`).textContent = labels[index]; });
    this.$('sharedMotionSizeGrid').dataset.shape = type;
    this.$('sharedMotionScaleFieldY').hidden = type === 'sphere';
    this.$('sharedMotionScaleFieldZ').hidden = type !== 'box';
    if (type !== 'sphere' && Number(this.$('sharedMotionObstacleSY').value) <= 0) this.$('sharedMotionObstacleSY').value = '0.4';
    if (type === 'box' && Number(this.$('sharedMotionObstacleSZ').value) <= 0) this.$('sharedMotionObstacleSZ').value = '0.4';
    this.$('sharedMotionObstacleSY').disabled = type === 'sphere';
    this.$('sharedMotionObstacleSZ').disabled = type !== 'box';
  }

  obstacleFromForm() {
    const obstacle_type = this.$('sharedMotionObstacleType').value;
    const pose = [...['X', 'Y', 'Z'].map((axis) => Number(this.$(`sharedMotionObstacle${axis}`).value)), 0, 0, 0, 1];
    let scale = ['SX', 'SY', 'SZ'].map((axis) => Number(this.$(`sharedMotionObstacle${axis}`).value));
    if (!pose.every(Number.isFinite) || !scale.every(Number.isFinite) || scale[0] <= 0) return null;
    if (obstacle_type === 'sphere') scale = [scale[0], 0, 0];
    if (obstacle_type === 'cylinder') scale = [scale[0], scale[1], 0];
    if ((obstacle_type === 'box' && !scale.every((item) => item > 0)) || (obstacle_type === 'cylinder' && scale[1] <= 0)) return null;
    return {
      obstacle_id: this.$('sharedMotionObstacleId').value.trim() || `obstacle_${this.candidateObstacles.length + 1}`,
      obstacle_type,
      pose,
      scale,
      target_frame: 'map',
    };
  }

  onObstacleEdited(obstacle) {
    if (!this.active) return;
    this.selectedCandidateId = obstacle.obstacle_id;
    const next = { ...obstacle };
    delete next.__draft;
    const index = this.candidateObstacles.findIndex((item) => item.obstacle_id === obstacle.obstacle_id);
    if (index >= 0) this.candidateObstacles[index] = next;
    this.writeObstacleForm(next);
    this.renderObstacles();
  }

  writeObstacleForm(obstacle) {
    this.$('sharedMotionObstacleId').value = obstacle.obstacle_id;
    this.$('sharedMotionObstacleType').value = obstacle.obstacle_type;
    this.updateObstacleScaleFields();
    ['X', 'Y', 'Z'].forEach((axis, index) => { this.$(`sharedMotionObstacle${axis}`).value = Number(obstacle.pose[index] || 0).toFixed(3); });
    ['SX', 'SY', 'SZ'].forEach((axis, index) => { this.$(`sharedMotionObstacle${axis}`).value = Number(obstacle.scale[index] || 0).toFixed(3); });
  }

  updateSelectedCandidateFromForm() {
    if (!this.selectedCandidateId) return;
    const next = this.obstacleFromForm();
    if (!next) return;
    const index = this.candidateObstacles.findIndex((item) => item.obstacle_id === this.selectedCandidateId);
    if (index < 0) return;
    this.candidateObstacles[index] = next;
    this.selectedCandidateId = next.obstacle_id;
    this.renderObstacles();
  }

  renderObstacles() {
    const list = this.$('sharedMotionObstacleList');
    list.replaceChildren();
    for (const item of this.candidateObstacles) {
      const doc = this.panelRoot.ownerDocument || document;
      const row = doc.createElement('div');
      row.className = `object-item${item.obstacle_id === this.selectedCandidateId ? ' selected' : ''}`;
      const button = doc.createElement('button');
      button.type = 'button';
      button.className = 'object-select';
      button.textContent = `${item.obstacle_id} · ${item.obstacle_type}`;
      button.onclick = () => {
        this.selectedCandidateId = item.obstacle_id;
        this.writeObstacleForm(item);
        this.scene.setMotionObstacleEditingEnabled?.(true);
        this.renderObstacles();
      };
      const remove = doc.createElement('button');
      remove.type = 'button';
      remove.className = 'btn small';
      remove.textContent = '×';
      remove.title = `删除候选碰撞体 ${item.obstacle_id}`;
      remove.setAttribute('aria-label', remove.title);
      remove.onclick = () => this.deleteCandidate(item.obstacle_id);
      row.append(button, remove);
      list.append(row);
    }
    if (!this.candidateObstacles.length) list.textContent = '尚无候选碰撞体';
    const rendered = [
      ...this.serviceObstacles,
      ...this.candidateObstacles.map((item) => (
        item.obstacle_id === this.selectedCandidateId ? { ...item, __candidate: true, __draft: true } : { ...item, __candidate: true }
      )),
    ];
    this.scene.setMotionObstacles?.(rendered, this.selectedCandidateId || this.selectedServiceObstacleId);
    this.$('sharedMotionDraftStatus').textContent = this.selectedCandidateId
      ? `编辑候选：${this.selectedCandidateId}（尚未加载到 Motion）`
      : '当前没有编辑中的碰撞体';
  }

  clearPickedObstaclePoints() {
    this.pickedObstaclePoints.splice(0);
    this.scene.cancelWorldPointCollection?.();
    this.scene.setMotionSelectionPoints?.([]);
    this.$('btnSharedMotionPickPoints').textContent = '开始多点选取';
    this.$('sharedMotionPickedStatus').textContent = '未选择点云点';
  }

  createCandidatePreview() {
    if (this.pickedObstaclePoints.length) return this.applyPickedPointBounds();
    const candidate = this.obstacleFromForm();
    if (!candidate) return this.renderStatus('碰撞体参数无效', true);
    const index = this.candidateObstacles.findIndex((item) => item.obstacle_id === candidate.obstacle_id);
    if (index >= 0) this.candidateObstacles[index] = candidate;
    else this.candidateObstacles.push(candidate);
    this.selectedCandidateId = candidate.obstacle_id;
    this.scene.setMotionObstacleEditingEnabled?.(true);
    this.renderObstacles();
    return true;
  }

  applyPickedPointBounds() {
    const type = this.$('sharedMotionObstacleType').value;
    const minimum = type === 'cylinder' ? 3 : 2;
    if (this.pickedObstaclePoints.length < minimum) return this.renderStatus(`${type} 至少需要 ${minimum} 个点`, true);
    const axes = ['x', 'y', 'z'];
    const min = axes.map((axis) => Math.min(...this.pickedObstaclePoints.map((point) => point[axis])));
    const max = axes.map((axis) => Math.max(...this.pickedObstaclePoints.map((point) => point[axis])));
    ['X', 'Y', 'Z'].forEach((axis, index) => { this.$(`sharedMotionObstacle${axis}`).value = ((min[index] + max[index]) / 2).toFixed(3); });
    let scale = axes.map((axis, index) => Math.max(max[index] - min[index], 0.01));
    const center = axes.map((axis, index) => (min[index] + max[index]) / 2);
    if (type === 'sphere') scale = [Math.max(0.01, ...this.pickedObstaclePoints.map((point) => Math.hypot(point.x - center[0], point.y - center[1], point.z - center[2]))), 0, 0];
    if (type === 'cylinder') scale = [Math.max(0.01, ...this.pickedObstaclePoints.map((point) => Math.hypot(point.x - center[0], point.y - center[1]))), Math.max(max[2] - min[2], 0.01), 0];
    ['SX', 'SY', 'SZ'].forEach((axis, index) => { this.$(`sharedMotionObstacle${axis}`).value = scale[index].toFixed(3); });
    const candidate = this.obstacleFromForm();
    if (!candidate) return false;
    const index = this.candidateObstacles.findIndex((item) => item.obstacle_id === candidate.obstacle_id);
    if (index >= 0) this.candidateObstacles[index] = candidate;
    else this.candidateObstacles.push(candidate);
    this.selectedCandidateId = candidate.obstacle_id;
    this.scene.setMotionObstacleEditingEnabled?.(true);
    this.renderObstacles();
    return true;
  }

  renderServiceObstacles() {
    const list = this.$('sharedMotionServiceObstacleList');
    list.replaceChildren();
    for (const item of this.serviceObstacles) {
      const button = (this.panelRoot.ownerDocument || document).createElement('button');
      button.type = 'button';
      button.className = 'btn small';
      button.textContent = item.obstacle_id;
      button.onclick = () => { this.selectedServiceObstacleId = item.obstacle_id; this.renderServiceObstacles(); this.renderObstacles(); };
      if (item.obstacle_id === this.selectedServiceObstacleId) button.classList.add('active');
      list.append(button);
    }
    if (!this.serviceObstacles.length) list.textContent = '服务端暂无碰撞体';
    this.$('motionServiceObstacleCount').textContent = String(this.serviceObstacles.length);
  }

  async refreshServiceObstacles() {
    const epoch = this.requestEpoch;
    try {
      const result = await this.client.command('motion', 'list_obstacles', {});
      if (!this.active || epoch !== this.requestEpoch) return;
      this.serviceObstacles = result.obstacles || [];
      if (!this.serviceObstacles.some((item) => item.obstacle_id === this.selectedServiceObstacleId)) this.selectedServiceObstacleId = null;
      this.renderServiceObstacles();
      this.renderObstacles();
    } catch (error) {
      if (this.active && epoch === this.requestEpoch) this.renderStatus(`查询服务端碰撞体失败：${error.message}`, true);
    }
  }

  async removeSelectedServiceObstacle() {
    const obstacleId = this.selectedServiceObstacleId;
    if (!obstacleId || !window.confirm(`删除服务端碰撞体 ${obstacleId}？`)) return;
    if (obstacleId === 'ground') return this.renderStatus('Motion 默认 ground 碰撞体不可删除', true);
    const epoch = this.requestEpoch;
    try {
      await this.client.command('motion', 'remove_obstacle', { obstacle_id: obstacleId });
      if (!this.active || epoch !== this.requestEpoch) return;
      this.selectedServiceObstacleId = null;
      await this.refreshServiceObstacles();
    } catch (error) {
      if (this.active && epoch === this.requestEpoch) this.renderStatus(`删除服务端碰撞体失败：${error.message}`, true);
    }
  }

  deleteCandidate(obstacleId) {
    this.candidateObstacles = this.candidateObstacles.filter((item) => item.obstacle_id !== obstacleId);
    if (this.selectedCandidateId === obstacleId) {
      this.selectedCandidateId = null;
      this.scene.setMotionObstacleEditingEnabled?.(false);
    }
    this.renderObstacles();
  }

  async loadSelectedCandidate() {
    const candidate = this.candidateObstacles.find((item) => item.obstacle_id === this.selectedCandidateId);
    if (!candidate) return this.renderStatus('请先选择候选碰撞体', true);
    const epoch = this.requestEpoch;
    try {
      const result = await this.client.command('motion', 'add_obstacle', candidate);
      if (!this.active || epoch !== this.requestEpoch) return;
      this.candidateObstacles = this.candidateObstacles.filter((item) => item.obstacle_id !== candidate.obstacle_id);
      this.selectedCandidateId = null;
      this.selectedServiceObstacleId = candidate.obstacle_id;
      this.scene.setMotionObstacleEditingEnabled?.(false);
      await this.refreshServiceObstacles();
      this._setStatus(result);
      this.renderStatus(result.dry_run ? `碰撞体 ${candidate.obstacle_id} 已加入脱机服务状态` : `碰撞体 ${candidate.obstacle_id} 已加载到 Motion`);
    } catch (error) {
      if (this.active && epoch === this.requestEpoch) this.renderStatus(`加载碰撞体失败：${error.message}`, true);
    }
  }

  _number(id, fallback = 0) {
    const value = Number(this.$(id).value);
    return Number.isFinite(value) ? value : fallback;
  }

  _payload() {
    const collisionCheck = this.$('motionCollisionCheck').checked;
    return {
      chain: this.$('motionChain').value,
      target_pose: [
        this._number('motionX'), this._number('motionY'), this._number('motionZ'),
        this._number('motionQx'), this._number('motionQy'), this._number('motionQz'), this._number('motionQw', 1),
      ],
      target_mode: 'absolute',
      position_offset: [0, 0, 0],
      frame_id: 'base_link',
      collision_check: collisionCheck,
      environment_collision_check: collisionCheck,
      obstacles: [],
      timeout_seconds: this._number('motionTimeout', 20),
    };
  }

  _setStatus(value) {
    this.$('motionStatus').textContent = typeof value === 'string' ? value : JSON.stringify(value, null, 2);
  }

  _setPlan(plan) {
    this.restorePlaybackPose();
    const valid = plan?.success && Array.isArray(plan.joint_names) && plan.joint_names.length
      && Array.isArray(plan.positions) && plan.positions.length
      && plan.positions.every((frame) => Array.isArray(frame) && frame.length === plan.joint_names.length);
    this.plan = valid ? plan : null;
    this.playbackIndex = 0;
    this.$('btnPlayMotionPlan').disabled = !this.plan;
    this.$('btnReplayMotionPlan').disabled = !this.plan;
    this.$('btnRestoreMotionPlan').disabled = !this.plan;
    this.$('btnExecuteMotionPlan').disabled = !this.plan?.plan_id;
    this._updatePlaybackProgress('准备播放');
    // A planned path is data only.  Keep the scene clean until playback or
    // frame inspection explicitly requests a visualization.
    this.scene.clearEndEffectorTrajectory?.();
    const state = this.$('motionTrajectoryInfo');
    state.classList.remove('success', 'failure', 'neutral');
    if (plan?.dry_run) {
      state.textContent = '规划未执行';
      state.classList.add('neutral');
      this.$('motionTrajectoryDetail').textContent = '脱机模式：请求已校验，但未调用真机 Motion 规划服务。';
    } else if (plan?.success) {
      state.textContent = '规划成功';
      state.classList.add('success');
      this.$('motionTrajectoryDetail').textContent = valid
        ? `已生成 ${plan.positions.length} 帧轨迹，可播放、逐帧查看或下发执行。`
        : '目标已满足，规划器没有返回需要播放的轨迹。';
    } else if (plan) {
      state.textContent = '规划失败';
      state.classList.add('failure');
      this.$('motionTrajectoryDetail').textContent = plan.detail || plan.status || '规划器未返回可用轨迹，请调整目标或碰撞条件。';
    } else {
      state.textContent = '等待规划';
      state.classList.add('neutral');
      this.$('motionTrajectoryDetail').textContent = '点击“规划并生成预览”查看本次结果';
    }
  }

  async planPreview() {
    const requestEpoch = ++this.requestEpoch;
    this._setPlan(null);
    this._setStatus('正在调用规划服务...');
    this.$('motionTrajectoryInfo').textContent = '正在规划';
    this.$('motionTrajectoryDetail').textContent = '正在请求 Motion 规划服务…';
    this.refreshTargetPreview();
    try {
      const result = await this.client.command('motion', 'plan_preview', this._payload());
      if (!this.active || requestEpoch !== this.requestEpoch) return;
      this._setStatus(result);
      this._setPlan(result);
      if (result.dry_run) this.renderStatus('Motion 请求已校验：脱机模式不调用规划器');
      else if (result.success && result.positions?.length) this.renderStatus(`规划完成：${result.positions.length} 个轨迹点，可在模型上预览`);
      else this.renderStatus(`规划未生成轨迹：${result.status || '未知状态'}`, true);
    } catch (error) {
      if (!this.active || requestEpoch !== this.requestEpoch) return;
      this._setStatus(`规划失败：${error.message}`);
      this.$('motionTrajectoryInfo').textContent = '规划失败';
      this.$('motionTrajectoryInfo').className = 'result-state failure';
      this.$('motionTrajectoryDetail').textContent = error.message || '规划请求失败，请检查连接后重试。';
      this.renderStatus(`Motion 规划失败：${error.message}`, true);
    }
  }

  togglePlayback() {
    if (this.playbackTimer) this.pausePlayback();
    else this.startPlayback(false);
  }

  startPlayback(restart) {
    if (!this.plan || !this.active) return;
    if (this.playbackTimer) this.pausePlayback();
    const { joint_names: names, positions } = this.plan;
    this._renderTrajectory();
    if (restart || this.playbackIndex >= positions.length) this.playbackIndex = 0;
    if (!this.restoreJointState) this.restoreJointState = this.scene.getJointState?.(names) || null;
    this.$('btnPlayMotionPlan').textContent = '暂停预览';
    const renderFrame = () => {
      if (!this.active || !this.plan) return this.restorePlaybackPose();
      if (this.playbackIndex >= positions.length) {
        this.restorePlaybackPose();
        this.scene.clearEndEffectorTrajectory?.();
        return;
      }
      this.scene.setJointState({ names, position: positions[this.playbackIndex] });
      this.scene.setEndEffectorTrajectoryCursor?.(this.playbackIndex, positions.length);
      this.playbackIndex += 1;
      this._updatePlaybackProgress('播放中');
      this.playbackTimer = window.setTimeout(renderFrame, 35);
    };
    renderFrame();
  }

  pausePlayback() {
    if (this.playbackTimer) window.clearTimeout(this.playbackTimer);
    this.playbackTimer = null;
    this.$('btnPlayMotionPlan').textContent = this.playbackIndex ? '继续预览' : '播放预览';
    this._updatePlaybackProgress('已暂停');
  }

  restorePlaybackPose() {
    this.pausePlayback();
    if (this.restoreJointState) this.scene.setJointState(this.restoreJointState);
    this.restoreJointState = null;
    this.playbackIndex = 0;
    this.scene.setEndEffectorTrajectoryCursor?.(0, this.plan?.positions?.length || 0);
    this._updatePlaybackProgress('已还原');
    this.runtime?.render();
  }

  showPlaybackFrame(index) {
    if (!this.plan || !this.active) return;
    this.pausePlayback();
    this._renderTrajectory();
    if (!this.restoreJointState) this.restoreJointState = this.scene.getJointState?.(this.plan.joint_names) || null;
    this.playbackIndex = Math.min(Math.max(0, index), this.plan.positions.length - 1);
    this.scene.setJointState({ names: this.plan.joint_names, position: this.plan.positions[this.playbackIndex] });
    this.scene.setEndEffectorTrajectoryCursor?.(this.playbackIndex, this.plan.positions.length);
    this._updatePlaybackProgress('查看帧');
  }

  _renderTrajectory() {
    if (!this.plan || !this.$('motionVisualizeTrajectory').checked) {
      this.scene.clearEndEffectorTrajectory?.();
      return;
    }
    const link = this.$('motionChain').value === 'right_arm' ? 'right_gripper_tcp_link' : 'left_gripper_tcp_link';
    this.scene.setEndEffectorTrajectory?.(this.plan.joint_names, this.plan.positions, link);
  }

  _updatePlaybackProgress(label) {
    const total = this.plan?.positions?.length || 0;
    const timeline = this.$('motionPlaybackTimeline');
    const progressContainer = this.$('motionPlaybackProgress');
    timeline.hidden = !total;
    progressContainer.hidden = !total;
    if (!total) return;
    const scrubber = this.$('motionPlaybackScrubber');
    scrubber.max = String(total - 1);
    scrubber.value = String(Math.min(this.playbackIndex, total - 1));
    this.$('motionPlaybackValue').textContent = `${scrubber.value} / ${total - 1} · ${label}`;
    progressContainer.querySelector('span').textContent = `${label} ${Math.min(this.playbackIndex, total)} / ${total}`;
    const progress = progressContainer.querySelector('progress');
    progress.max = total;
    progress.value = Math.min(this.playbackIndex, total);
  }

  async execute() {
    if (!this.plan?.plan_id) return;
    if (!this.$('motionExecuteEnabled').checked) {
      this.renderStatus('请先启用“允许将规划同步到机器人”开关', true);
      return;
    }
    if (!window.confirm('确认将当前规划同步到机器人并执行？系统会再次核对真机关节是否仍位于规划起点。')) return;
    const epoch = this.requestEpoch;
    try {
      const result = await this.client.command('motion', 'execute_plan', {
        plan_id: this.plan.plan_id,
        confirmed: true,
        speed_rad_s: this._number('motionSpeed', 0.15),
        start_tolerance_rad: this._number('motionStartTolerance', 0.05),
      });
      if (!this.active || epoch !== this.requestEpoch) return;
      this._setStatus(result);
      this.renderStatus(result.success ? '实机轨迹执行完成' : `实机轨迹未执行：${result.status}`, !result.success);
      if (result.success) this.$('btnExecuteMotionPlan').disabled = true;
      if (result.success) {
        this.scene.clearEndEffectorTrajectory?.();
        this.restoreJointState = null;
        this.playbackIndex = 0;
      }
    } catch (error) {
      if (!this.active || epoch !== this.requestEpoch) return;
      this._setStatus(`执行失败：${error.message}`);
      this.renderStatus(`实机执行已拒绝：${error.message}`, true);
    }
  }
}
