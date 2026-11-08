// Coordinates workspace-specific scene behavior while the underlying WebGL
// scene, robot model, map layers, and SDK subscriptions stay alive.
export class WorkspaceSceneHost {
  constructor(scene) {
    this.scene = scene;
    this.profiles = new Map();
    this.activeName = null;
  }

  register(name, profile = {}) {
    if (!name) throw new Error('workspace name is required');
    if (this.profiles.has(name)) throw new Error(`workspace ${name} is already registered`);
    this.profiles.set(name, profile);
    return () => {
      if (this.activeName === name) this.deactivate();
      profile.dispose?.(this._context(name));
      this.profiles.delete(name);
    };
  }

  activate(name) {
    if (name === this.activeName) return;
    if (!this.profiles.has(name)) throw new Error(`workspace ${name} is not registered`);
    const previous = this.profiles.get(this.activeName);
    const previousName = this.activeName;
    this.activeName = null;
    try {
      previous?.deactivate?.(this._context(previousName));
    } finally {
      this._clearTransientInteraction();
    }
    try {
      this.profiles.get(name)?.activate?.(this._context(name));
      this.activeName = name;
    } catch (error) {
      this._clearTransientInteraction();
      throw error;
    }
  }

  deactivate() {
    if (!this.activeName) return;
    const name = this.activeName;
    this.activeName = null;
    try {
      this.profiles.get(name)?.deactivate?.(this._context(name));
    } finally {
      this._clearTransientInteraction();
    }
  }

  dispose() {
    let firstError = null;
    try { this.deactivate(); } catch (error) { firstError = error; }
    for (const [name, profile] of this.profiles) {
      try { profile.dispose?.(this._context(name)); } catch (error) { firstError ||= error; }
    }
    this.profiles.clear();
    if (firstError) throw firstError;
  }

  _context(name) {
    return { name, scene: this.scene };
  }

  _clearTransientInteraction() {
    // Motion point selection, obstacle gizmos, and navigation goal picking
    // are page-local tools. Never let one remain armed after a workspace swap.
    this.scene.cancelWorldPointCollection?.();
    this.scene.setMotionObstacleEditingEnabled?.(false);
    this.scene.onPointPick = null;
    this.scene.onPointPickMiss = () => {};
    this.scene.onPointCollectionCancelled = () => {};
    this.scene.enableMotionObstacleEditing?.(null);
    this.scene.onGoalChange = () => {};
    this.scene.onGoalCommit = () => {};
    this.scene.onNodeSelect = () => {};
    if (this.scene.mode !== 'pan') this.scene.setMode('pan');
  }
}
