import { NavigationStore } from '../domain/navigation-store.js';
import { RobotStore } from '../domain/robot-store.js';
import { SceneMapView } from './scene-map.js';
import { applyScenePreferences, loadScenePreferences } from './scene-preferences.js';
import { RobotWorkspaceController } from '../features/robot/workspace-controller.js';
import { WorkspaceSceneHost } from './workspace-scene-host.js';

// Shared scene infrastructure for every workspace page. Feature modules own
// their controls, while this runtime owns scene data and lifecycle ordering.
export class SharedSceneRuntime {
  constructor({ client, canvas, status = () => {}, scenePreferences = loadScenePreferences(), fallbackModel = 'g1' }) {
    this.client = client;
    this.scene = new SceneMapView(canvas);
    this.workspaceHost = new WorkspaceSceneHost(this.scene);
    this.navigationStore = new NavigationStore();
    this.robotStore = new RobotStore();
    this.robot = new RobotWorkspaceController({
      client,
      scene: this.scene,
      store: this.robotStore,
      renderStatus: status,
      displayMode: scenePreferences.robotDisplayMode,
    });
    this.status = status;
    this.scenePreferences = scenePreferences;
    this.fallbackModel = fallbackModel;
    this.unsubscribe = null;
    this.startPromise = null;
    this.resizeObserver = null;
    this.resizeHandler = null;
    this.disposed = false;
  }

  start(options = {}) {
    if (this.disposed) return Promise.reject(new Error('SharedSceneRuntime has been disposed'));
    if (!this.startPromise) this.startPromise = this._start(options);
    return this.startPromise;
  }

  async _start({ loadModel = true, loadMap = true, fit = true, getRenderOptions = null, onEvent = null, onError = null } = {}) {
    this.getRenderOptions = getRenderOptions;
    applyScenePreferences(this.scene, this.scenePreferences);
    this._observeResize(fit);

    // Model selection belongs to static scene bootstrap, not live SDK state.
    // Load it first so a connection or authentication failure cannot leave a
    // workspace with an empty robot scene.
    let bootstrapModel = null;
    if (loadModel) {
      try {
        const bootstrap = await Promise.race([
          this.client.getSceneBootstrap(),
          new Promise((_, reject) => window.setTimeout(() => reject(new Error('场景配置查询超时')), 1500)),
        ]);
        bootstrapModel = bootstrap?.model || this.fallbackModel;
        if (bootstrapModel) await this.robot.loadModel(bootstrapModel);
      } catch (error) {
        bootstrapModel = this.fallbackModel;
        try {
          await this.robot.loadModel(bootstrapModel);
          this.status(`场景配置不可用，已使用 ${bootstrapModel.toUpperCase()} 静态模型: ${error.message}`);
        } catch (modelError) {
          this.status(`机器人模型加载失败: ${modelError.message}`);
        }
      }
    }

    // Map loading is also static and must not gate model loading.
    const mapTask = loadMap && this.scenePreferences.layers.pcd !== false
      ? this.robot.loadStaticGlobalCloud({ fit }).catch((error) => {
        this.status(`静态地图点云加载失败: ${error.message}`);
        return false;
      })
      : Promise.resolve(false);
    let capabilities = null;
    try {
      capabilities = await this.client.getCapabilities();
      this.robot.capabilities = capabilities;
    } catch (error) {
      this.status(`SDK 能力不可用，保留静态场景: ${error.message}`);
    }
    if (loadModel && capabilities?.model && capabilities.model !== bootstrapModel) {
      try { await this.robot.loadModel(capabilities.model); }
      catch (error) { this.status(`机器人模型加载失败，保留其他场景层: ${error.message}`); }
    }
    await mapTask;
    this.scene.fit();
    try { this.navigationStore.update(await this.client.getState()); }
    catch (error) { this.status(`导航状态暂不可用: ${error.message}`); }
    try { await this.robot.refresh(); }
    catch (error) { this.status(`机器人状态暂不可用: ${error.message}`); }
    if (this.disposed) throw new Error('SharedSceneRuntime was disposed during startup');
    this.render();
    this.unsubscribe = this.client.subscribe((event) => {
      this.navigationStore.consume(event);
      this.robotStore.consume(event);
      this.render();
      onEvent?.(event, this);
    }, (error) => {
      this.status(`共享场景事件连接失败: ${error.message || 'WebSocket error'}`);
      onError?.(error, this);
    });
    return { capabilities, scene: this.scene, robot: this.robot, robotStore: this.robotStore, navigationStore: this.navigationStore };
  }

  render() {
    const options = this.getRenderOptions?.();
    if (options !== false) this.robot.render(this.robotStore.state, this.navigationStore.state, options || {});
  }

  registerWorkspace(name, profile) {
    return this.workspaceHost.register(name, profile);
  }

  activateWorkspace(name) {
    this.workspaceHost.activate(name);
  }

  _observeResize(fit) {
    if (!fit) return;
    const target = this.scene.canvas?.parentElement || this.scene.canvas;
    if (!target) return;
    this.resizeHandler = () => this.scene.fit();
    window.addEventListener('resize', this.resizeHandler);
    if (this.resizeObserver) return;
    this.resizeObserver = new ResizeObserver(this.resizeHandler);
    this.resizeObserver.observe(target);
    requestAnimationFrame(() => requestAnimationFrame(this.resizeHandler));
  }

  dispose() {
    if (this.disposed) return;
    this.disposed = true;
    let profileError = null;
    try { this.workspaceHost.dispose(); } catch (error) { profileError = error; }
    try { this.unsubscribe?.(); } finally {
      this.unsubscribe = null;
      this.resizeObserver?.disconnect();
      this.resizeObserver = null;
      if (this.resizeHandler) window.removeEventListener('resize', this.resizeHandler);
      this.resizeHandler = null;
    }
    if (profileError) throw profileError;
  }
}
