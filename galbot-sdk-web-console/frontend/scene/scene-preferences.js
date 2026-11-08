const STORAGE_KEY = 'galbot.scene.preferences.v1';

const DEFAULTS = {
  layers: {
    grid: true,
    laser: true,
    localCloud: true,
    globalCloud: true,
    pcd: true,
    path: true,
    collisionRobot: true,
    attachObjects: true,
  },
  viewMode: '3d',
  robotDisplayMode: 'visual',
  staticPointSize: 2,
  lidarPointSize: 3,
  robotOverlays: {
    power: false,
    pose: false,
    leg: false,
    head: false,
    left_arm: false,
    right_arm: false,
  },
};

export function loadScenePreferences() {
  try {
    const parsed = JSON.parse(localStorage.getItem(STORAGE_KEY) || '{}');
    return {
      ...DEFAULTS,
      ...parsed,
      layers: { ...DEFAULTS.layers, ...(parsed.layers || {}) },
      robotOverlays: { ...DEFAULTS.robotOverlays, ...(parsed.robotOverlays || {}) },
    };
  } catch (_) {
    return { ...DEFAULTS, layers: { ...DEFAULTS.layers }, robotOverlays: { ...DEFAULTS.robotOverlays } };
  }
}

export function saveScenePreferences(partial = {}) {
  const next = {
    ...loadScenePreferences(),
    ...partial,
  };
  if (partial.layers) next.layers = { ...loadScenePreferences().layers, ...partial.layers };
  if (partial.robotOverlays) next.robotOverlays = { ...loadScenePreferences().robotOverlays, ...partial.robotOverlays };
  try { localStorage.setItem(STORAGE_KEY, JSON.stringify(next)); } catch (_) { /* storage unavailable */ }
  return next;
}

export function applyScenePreferences(scene, preferences = loadScenePreferences()) {
  Object.entries(preferences.layers || {}).forEach(([layer, visible]) => scene.setLayerVisible(layer, visible));
  scene.setViewMode(preferences.viewMode === 'top' ? 'top' : '3d');
  scene.setCloudViewOptions({ staticPointSizePx: Number(preferences.staticPointSize) || 2, localPointSizePx: Number(preferences.lidarPointSize) || 3 });
  return preferences;
}
