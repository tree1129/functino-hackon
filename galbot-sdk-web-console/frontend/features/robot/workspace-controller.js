import { quaternionToYaw } from '../../domain/pose.js';
import { loadPCD } from '../../scene/vendor/pcd-loader.js';

export class RobotWorkspaceController {
  constructor({ client, scene, store, renderStatus, displayMode = 'visual' }) {
    this.client = client;
    this.scene = scene;
    this.store = store;
    this.renderStatus = renderStatus;
    this.capabilities = null;
    this.model = null;
    this.displayMode = displayMode === 'collision' ? 'collision' : 'visual';
  }

  async configure(capabilities) {
    this.capabilities = capabilities;
    this.model = capabilities?.model || this.model;
    await this.loadModel();
  }

  async loadModel(model = this.model || this.capabilities?.model) {
    if (!model) return;
    this.model = model;
    const response = await fetch(`/assets/robots/${model}/manifest.json`);
    if (!response.ok) throw new Error(`机器人资产清单不可用: ${response.status}`);
    const manifest = await response.json();
    const resolveMeshUrl = (uri) => {
      for (const [packageName, relativeRoot] of Object.entries(manifest.package_roots || {})) {
        const prefix = `package://${packageName}/`;
        if (uri.startsWith(prefix)) return `/assets/robots/${model}/${relativeRoot}/${uri.slice(prefix.length)}`;
      }
      return uri;
    };
    const mode = this.displayMode;
    const urdf = mode === 'collision' ? manifest.collision_urdf : manifest.visual_urdf;
    if (!urdf) throw new Error(`${mode === 'collision' ? '碰撞' : '视觉'}模型资产不可用`);
    await this.scene.loadUrdfUrl(`/assets/robots/${model}/${urdf}`, { displayMode: mode, resolveMeshUrl });
    this.renderStatus(`已加载 ${model.toUpperCase()} ${mode === 'collision' ? '碰撞简化' : '精细视觉'}模型`);
  }

  setDisplayMode(mode) {
    this.displayMode = mode === 'collision' ? 'collision' : 'visual';
  }

  async refresh() {
    const snapshot = await this.client.command('robot', 'snapshot', { include_lidar: false });
    this.store.update({ jointState: snapshot.joint_state, localCloud: snapshot.lidar });
    this.renderStatus(snapshot.dry_run ? '机器人状态：脱机预览' : '机器人状态：已刷新');
  }

  async loadStaticGlobalCloud({ fit = true } = {}) {
    const response = await fetch('/api/maps/global-cloud');
    if (response.status === 404) {
      this.renderStatus('静态地图点云不可用：未找到 /var/maps/cur/global_cloud_cleaned.pcd');
      return false;
    }
    if (!response.ok) throw new Error(`静态地图点云加载失败: ${response.status}`);
    const data = await loadPCD(await response.blob());
    const points = Array.from({ length: data.count }, (_, index) => ({
      x: data.positions[index * 3],
      y: data.positions[index * 3 + 1],
      z: data.positions[index * 3 + 2],
    }));
    this.scene.setPcdPoints(points, { color: 0x8495a7, size: 2, maxPoints: 350000, fit });
    this.renderStatus(`已加载静态地图点云：${data.count.toLocaleString()} 点`);
    return true;
  }

  async loadLocalPcd(file, { fit = true } = {}) {
    const data = await loadPCD(file);
    const points = Array.from({ length: data.count }, (_, index) => ({
      x: data.positions[index * 3],
      y: data.positions[index * 3 + 1],
      z: data.positions[index * 3 + 2],
    }));
    this.scene.setPcdPoints(points, { color: 0x8495a7, size: 2, maxPoints: 350000, fit });
    this.renderStatus(`已加载本地 PCD：${data.count.toLocaleString()} 点`);
    return data.count;
  }

  async loadLocalUrdf(file) {
    await this.scene.loadUrdfText(await file.text(), file.name);
    this.renderStatus(`已加载本地 URDF：${file.name}`);
  }

  render(state, navigationState, options = {}) {
    if (navigationState?.pose) this.scene.setRobotPose({ ...navigationState.pose, yaw: quaternionToYaw(navigationState.pose) });
    if (options.joints !== false && state.jointState) this.scene.setJointState({
      names: state.jointState.map((joint) => joint.name),
      position: state.jointState.map((joint) => joint.position),
    });
    if (state.localCloud) {
      const status = document.getElementById('lidarFrameStatus');
      if (state.localCloud.transform_error) {
        this.scene.clearLocalCloud();
        if (status) {
          status.textContent = `${state.localCloud.source_frame_id || '未知坐标系'}：${state.localCloud.transform_error}`;
          status.classList.add('error');
        }
      } else {
        this.scene.setLocalCloud(state.localCloud);
        if (status) {
          const source = state.localCloud.source_frame_id || '未知坐标系';
          status.textContent = source.replace(/^\//, '').toLowerCase() === 'map'
            ? `点云已是 map 坐标；消息中没有独立雷达坐标系 · ${Number(state.localCloud.selected_point_count || 0).toLocaleString()} 点`
            : `${source} -> map · ${Number(state.localCloud.selected_point_count || 0).toLocaleString()} 点`;
          status.classList.remove('error');
        }
      }
    }
  }
}
