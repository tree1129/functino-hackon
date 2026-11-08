import * as THREE from 'three';
import { OrbitControls } from 'three/addons/controls/OrbitControls.js';
import { Line2 } from 'three/addons/lines/Line2.js';
import { LineGeometry } from 'three/addons/lines/LineGeometry.js';
import { LineMaterial } from 'three/addons/lines/LineMaterial.js';
import { UrdfModel } from './vendor/urdf-viewer.js';
import { loadPCD } from './vendor/pcd-loader.js';
import { sampleEdgePath } from './vendor/edge-geometry.js';

const COLLISION_GHOST_COLOR = 0xff3b3b;
const COLLISION_GHOST_OPACITY = 0.38;
const COORDINATE_AXIS_LENGTH = 0.42;
const COORDINATE_AXIS_OPACITY = 0.82;
const COORDINATE_AXIS_COLORS = {
  x: 0xff3b30,
  y: 0x34c759,
  z: 0x0a84ff,
};

/**
 * Unified top-down scene: occupancy / laser / path / topo / PCD + URDF robot.
 * Keeps MapView-compatible API used by app.js.
 */
export class SceneMapView {
  constructor(canvas) {
    this.canvas = canvas;
    this.mode = 'pan';
    this.viewMode = '3d'; // '3d' | 'top'
    this.points = [];
    this.topo = null;
    this.goal = null;
    this.robot = null;
    this.robotVisible = true;
    this.followRobot = false;
    this.autoFrame = true;
    this.selectedNodeId = null;
    this.bodyJointNames = [];
    this.bounds = { minX: -5, maxX: 5, minY: -5, maxY: 5 };
    this.onGoalChange = () => {};
    this.onGoalCommit = () => {};
    this.onGoalCancel = () => {};
    this.resolveGoalPoint = (goal) => goal;
    this.onNodeSelect = () => {};
    this.onAfterRender = () => {};
    this.onPointPick = null;
    this.onPointPickMiss = () => {};
    this.onMotionObstacleEdit = null;
    this.onFocusRobot = () => this.focusRobot();
    this.motionObstacleEditingEnabled = false;
    this._motionObstacleDrag = null;
    this._motionDraft = null;
    this.onPointCollectionCancelled = () => {};

    this.renderer = new THREE.WebGLRenderer({ canvas, antialias: true });
    this.renderer.setPixelRatio(devicePixelRatio);
    this.scene = new THREE.Scene();
    this.scene.background = new THREE.Color(0x1a1d23);

    this.perspectiveCamera = new THREE.PerspectiveCamera(50, 1, 0.05, 500);
    this.perspectiveCamera.up.set(0, 0, 1);
    this.perspectiveCamera.position.set(0, -8, 10);
    this.topCamera = new THREE.OrthographicCamera(-10, 10, 10, -10, 0.05, 500);
    this.topCamera.up.set(0, 1, 0);
    this.topCamera.position.set(0, 0, 20);
    this._topHalfHeight = 10;
    this.camera = this.perspectiveCamera;
    this.controls = new OrbitControls(this.camera, canvas);
    this.controls.enableDamping = true;
    this.controls.dampingFactor = 0.08;
    this.controls.screenSpacePanning = true;
    this.controls.maxPolarAngle = Math.PI * 0.48;
    this.controls.minDistance = 1;
    this.controls.maxDistance = 120;
    this.controls.target.set(0, 0, 0);
    this.controls.update();
    // User pan/zoom takes ownership of the camera — stop auto framing / robot follow.
    this.controls.addEventListener('start', () => {
      this.autoFrame = false;
      this.followRobot = false;
    });
    const hemi = new THREE.HemisphereLight(0xffffff, 0x2a3340, 1.0);
    this.scene.add(hemi);
    const dir = new THREE.DirectionalLight(0xffffff, 0.7);
    dir.position.set(4, -2, 8);
    this.scene.add(dir);

    this.groundGrid = new THREE.GridHelper(40, 40, 0x3a4150, 0x2a303a);
    this.groundGrid.rotation.x = Math.PI / 2;
    this.scene.add(this.groundGrid);

    this.coordinateFrameVisible = { map: true, base: true };
    this.coordinateFrames = {
      map: this._createCoordinateFrame('map'),
      base: this._createCoordinateFrame('base_link'),
    };
    this.coordinateFrames.map.position.z = 0.015;
    Object.values(this.coordinateFrames).forEach((frame) => this.scene.add(frame));

    this.layer = {
      pcd: null,
      grid: null,
      laser: null,
      localCloud: null,
      globalCloud: null,
      path: null,
      motionTrajectory: null,
      motionObstacles: new THREE.Group(),
      motionPointSelection: new THREE.Group(),
      motionObstacleGizmo: new THREE.Group(),
      navigationWaypoints: new THREE.Group(),
      topo: new THREE.Group(),
      measure: new THREE.Group(),
      measureCursor: new THREE.Group(),
      goal: null,
    };
    this.cloudView = {
      localMinZ: -Infinity,
      localMaxZ: Infinity,
      localMaxPoints: 8000,
      localCrop: null,
      globalMinZ: 0,
      globalMaxZ: 2,
      pointSizePx: 3,
      staticPointSizePx: 2,
      localPointSizePx: 3,
    };
    this.layerVisible = {
      grid: true,
      laser: true,
      localCloud: true,
      globalCloud: true,
      pcd: true,
      path: true,
      collisionRobot: true,
      attachObjects: true,
    };
    this._localCloudRaw = null;
    this._globalCloudRaw = null;
    this._pcdRaw = null;
    this._staticCropLastCenter = null;
    this.staticCloudView = { crop: null };
    this._pathRaw = null;
    this._attachObjectsRaw = [];
    this._attachUseSpheres = true;
    this.pathWidthPx = 4;
    this.scene.add(this.layer.topo);
    this.scene.add(this.layer.measure);
    this.scene.add(this.layer.measureCursor);
    this.scene.add(this.layer.motionObstacles);
    this.scene.add(this.layer.motionPointSelection);
    this.scene.add(this.layer.motionObstacleGizmo);
    this.scene.add(this.layer.navigationWaypoints);
    this.urdfModel = null;
    this.collisionUrdfModel = null;
    this.robotMarker = null;
    this._collisionGhostBaseOpacity = COLLISION_GHOST_OPACITY;
    this._collisionGhostFadeMs = 3000;
    this._collisionGhostLastMs = 0;
    this._collisionGhostAlive = false;
    this.onCollisionGhostTimeout = null;

    this._raycaster = new THREE.Raycaster();
    this._plane = new THREE.Plane(new THREE.Vector3(0, 0, 1), 0);
    this._ndc = new THREE.Vector2();
    this._dragGoal = null;
    this._bindInput();

    this._running = true;
    const tick = () => {
      if (!this._running) return;
      requestAnimationFrame(tick);
      this._updateCollisionGhostFade();
      this.controls.update();
      this.renderer.render(this.scene, this.camera);
      try { this.onAfterRender(); } catch (_) {}
    };
    tick();
    this.fit();
  }

  _createCoordinateFrame(label) {
    const group = new THREE.Group();
    const origin = new THREE.Vector3(0, 0, 0);
    const axes = [
      [new THREE.Vector3(1, 0, 0), COORDINATE_AXIS_COLORS.x],
      [new THREE.Vector3(0, 1, 0), COORDINATE_AXIS_COLORS.y],
      [new THREE.Vector3(0, 0, 1), COORDINATE_AXIS_COLORS.z],
    ];
    axes.forEach(([direction, color]) => {
      const arrow = new THREE.ArrowHelper(
        direction,
        origin,
        COORDINATE_AXIS_LENGTH,
        color,
        0.11,
        0.06,
      );
      arrow.traverse((child) => {
        if (!child.material) return;
        child.material.transparent = true;
        child.material.opacity = COORDINATE_AXIS_OPACITY;
        child.material.depthWrite = false;
      });
      group.add(arrow);
    });

    const canvas = document.createElement('canvas');
    canvas.width = 256;
    canvas.height = 64;
    const texture = new THREE.CanvasTexture(canvas);
    const material = new THREE.SpriteMaterial({ map: texture, depthTest: false, transparent: true, opacity: 0.88 });
    const sprite = new THREE.Sprite(material);
    sprite.position.set(COORDINATE_AXIS_LENGTH + 0.14, 0, 0.045);
    sprite.scale.set(0.58, 0.145, 1);
    sprite.renderOrder = 50;
    group.add(sprite);
    group.userData.labelSprite = sprite;
    this._setCoordinateFrameLabel(group, label);
    return group;
  }

  _setCoordinateFrameLabel(group, label) {
    const sprite = group?.userData?.labelSprite;
    const canvas = sprite?.material?.map?.image;
    const context = canvas?.getContext?.('2d');
    if (!context) return;
    const text = String(label || '').replace(/^\//, '') || 'unknown';
    context.clearRect(0, 0, canvas.width, canvas.height);
    context.font = 'bold 22px sans-serif';
    context.fillStyle = 'rgba(248, 250, 252, 0.92)';
    context.textAlign = 'center';
    context.textBaseline = 'middle';
    context.fillText(text, canvas.width / 2, canvas.height / 2);
    sprite.material.map.needsUpdate = true;
    group.userData.label = text;
  }

  _setFramePose(group, pose) {
    if (!group || !pose) return;
    const value = (index, fallback) => Number.isFinite(Number(pose[index])) ? Number(pose[index]) : fallback;
    group.position.set(value(0, 0), value(1, 0), value(2, 0));
    group.quaternion.set(value(3, 0), value(4, 0), value(5, 0), value(6, 1)).normalize();
  }

  setCoordinateFrameVisible(frame, visible) {
    if (!(frame in this.coordinateFrameVisible)) return;
    this.coordinateFrameVisible[frame] = !!visible;
    const group = this.coordinateFrames[frame];
    group.visible = !!visible;
  }

  /** Project world XY(Z) to CSS pixels relative to the canvas element. */
  projectWorldToCanvas(x, y, z = 0) {
    const v = new THREE.Vector3(x, y, z);
    v.project(this.camera);
    if (!Number.isFinite(v.x) || !Number.isFinite(v.y) || v.z > 1) return null;
    const w = this.canvas.clientWidth;
    const h = this.canvas.clientHeight;
    return {
      x: (v.x * 0.5 + 0.5) * w,
      y: (-v.y * 0.5 + 0.5) * h,
    };
  }

  clearTopoSelection() {
    this.selectedNodeId = null;
    this._refreshTopoSelection();
  }

  fit() {
    const w = Math.max(1, this.canvas.clientWidth);
    const h = Math.max(1, this.canvas.clientHeight);
    this.renderer.setSize(w, h, false);
    const aspect = w / h;
    if (this.camera.isOrthographicCamera) {
      const halfHeight = Math.max(0.1, this._topHalfHeight || 10);
      this.camera.left = -halfHeight * aspect;
      this.camera.right = halfHeight * aspect;
      this.camera.top = halfHeight;
      this.camera.bottom = -halfHeight;
    } else {
      this.camera.aspect = aspect;
    }
    this.camera.updateProjectionMatrix();
    if (this.layer.path?.material?.resolution) {
      this.layer.path.material.resolution.set(w, h);
    }
    if (this.layer.motionTrajectory?.line?.material?.resolution) {
      this.layer.motionTrajectory.line.material.resolution.set(w, h);
    }
  }

  setMode(mode) {
    this.mode = mode;
    this._applyControlsForMode();
    this.canvas.style.cursor = mode === 'goal' || mode === 'initialPose' || mode === 'measure'
      ? 'crosshair' : mode === 'topo' ? 'pointer' : 'grab';
  }

  setViewMode(viewMode) {
    this.viewMode = viewMode === 'top' ? 'top' : '3d';
    if (this.viewMode === 'top') this.setTopView();
    else this.set3dView();
    this._applyControlsForMode();
  }

  _applyControlsForMode() {
    this.controls.enabled = true;
    this.controls.enableZoom = true;
    this.controls.enablePan = true;
    if (this.mode === 'pan') {
      // 3D 由 OrbitControls 旋转；俯视默认左键平移，Shift + 左键
      // 的平面旋转由 _bindInput 在捕获阶段接管。右键始终用于平移。
      this.controls.enableRotate = this.viewMode === '3d';
      this.controls.mouseButtons = {
        LEFT: this.viewMode === '3d' ? THREE.MOUSE.ROTATE : THREE.MOUSE.PAN,
        MIDDLE: THREE.MOUSE.DOLLY,
        RIGHT: THREE.MOUSE.PAN,
      };
    } else {
      this.controls.enableRotate = false;
      this.controls.mouseButtons = {
        LEFT: null,
        MIDDLE: THREE.MOUSE.DOLLY,
        RIGHT: THREE.MOUSE.PAN,
      };
    }
  }

  setPoints(points) {
    this.points = points || [];
    this._remove(this.layer.pcd);
    this.layer.pcd = null;
    if (!this.points.length) return;
    let minX = Infinity, maxX = -Infinity, minY = Infinity, maxY = -Infinity;
    const positions = new Float32Array(this.points.length * 3);
    const step = Math.max(1, Math.floor(this.points.length / 120000));
    let n = 0;
    for (let i = 0; i < this.points.length; i += step) {
      const p = this.points[i];
      positions[n * 3] = p.x;
      positions[n * 3 + 1] = p.y;
      positions[n * 3 + 2] = 0.02;
      minX = Math.min(minX, p.x); maxX = Math.max(maxX, p.x);
      minY = Math.min(minY, p.y); maxY = Math.max(maxY, p.y);
      n += 1;
    }
    const geo = new THREE.BufferGeometry();
    geo.setAttribute('position', new THREE.BufferAttribute(positions.slice(0, n * 3), 3));
    this.layer.pcd = new THREE.Points(geo, new THREE.PointsMaterial({ color: 0x78a0c8, size: 0.04 }));
    this.scene.add(this.layer.pcd);
    this.bounds = { minX: minX - 1, maxX: maxX + 1, minY: minY - 1, maxY: maxY + 1 };
    if (this.autoFrame) this._frameBounds();
  }

  setRemotePoints(points) {
    this.setGlobalCloud({ points: Array.isArray(points) ? points : [] });
  }

  /** Static XYZ PCD for tools that need true 3D height (Pipeline, editors). */
  setPcdPoints(points, opts = {}) {
    if (!opts.keepRaw) this._pcdRaw = Array.isArray(points) ? points : [];
    const pts = opts.keepRaw ? (this._pcdRaw || []) : this._pcdRaw;
    this.points = pts;
    const cropConfig = opts.crop ?? this.staticCloudView.crop;
    const crop = cropConfig ? { ...this._staticCloudCropCenter(), ...cropConfig } : undefined;
    this.setCloudPoints('pcd', pts, {
      color: opts.color ?? 0x78a0c8,
      size: opts.size ?? (this.cloudView?.staticPointSizePx || 2),
      maxPoints: opts.maxPoints ?? 350000,
      minZ: opts.minZ ?? -Infinity,
      maxZ: opts.maxZ ?? Infinity,
      crop,
    });
    if (!pts.length) return;
    let minX = Infinity, maxX = -Infinity, minY = Infinity, maxY = -Infinity;
    for (const p of pts) {
      if (!Number.isFinite(p.x) || !Number.isFinite(p.y)) continue;
      minX = Math.min(minX, p.x); maxX = Math.max(maxX, p.x);
      minY = Math.min(minY, p.y); maxY = Math.max(maxY, p.y);
    }
    if (!Number.isFinite(minX)) return;
    this.bounds = { minX: minX - 1, maxX: maxX + 1, minY: minY - 1, maxY: maxY + 1 };
    if (opts.fit !== false) this.fitToBounds();
  }

  setStaticCloudViewOptions(partial = {}) {
    this.staticCloudView = { ...(this.staticCloudView || {}), ...partial };
    this._staticCropLastCenter = null;
    if (this._pcdRaw) this.setPcdPoints(this._pcdRaw, { keepRaw: true, fit: false });
  }

  _staticCloudCropCenter() {
    return { centerX: this.robot?.x || 0, centerY: this.robot?.y || 0, centerZ: this.robot?.z || 0 };
  }

  _refreshStaticCloudCrop() {
    if (!this.staticCloudView?.crop || !this._pcdRaw?.length) return;
    const center = this._staticCloudCropCenter();
    const previous = this._staticCropLastCenter;
    if (previous && Math.hypot(center.centerX - previous.centerX, center.centerY - previous.centerY, center.centerZ - previous.centerZ) < 0.25) return;
    this._staticCropLastCenter = center;
    this.setPcdPoints(this._pcdRaw, { keepRaw: true, fit: false });
  }

  /**
   * Render XYZ points as THREE.Points.
   * size is absolute screen pixels (sizeAttenuation=false) so top-down zoom still shows size changes.
   * @param {'localCloud'|'globalCloud'|'pcd'} layerKey
   * @param {{x:number,y:number,z?:number}[]} points
   * @param {{ color?: number, size?: number, maxPoints?: number, liftZ?: number, minZ?: number, maxZ?: number, crop?: {centerX:number,centerY:number,centerZ:number,halfX:number,halfY:number,halfZ:number} }} [opts]
   */
  setCloudPoints(layerKey, points, opts = {}) {
    const key = layerKey || 'pcd';
    this._remove(this.layer[key]);
    this.layer[key] = null;
    const pts = Array.isArray(points) ? points : [];
    if (!pts.length) return;
    const minZ = opts.minZ != null && Number.isFinite(opts.minZ) ? opts.minZ : -Infinity;
    const maxZ = opts.maxZ != null && Number.isFinite(opts.maxZ) ? opts.maxZ : Infinity;
    const crop = opts.crop;
    const filtered = [];
    for (let i = 0; i < pts.length; i++) {
      const p = pts[i];
      const z = Number.isFinite(p.z) ? p.z : 0;
      if (z < minZ || z > maxZ) continue;
      if (crop && (
        Math.abs(p.x - crop.centerX) > crop.halfX ||
        Math.abs(p.y - crop.centerY) > crop.halfY ||
        Math.abs(z - crop.centerZ) > crop.halfZ
      )) continue;
      filtered.push(p);
    }
    if (!filtered.length) return;
    const maxPoints = Math.max(1000, opts.maxPoints || 150000);
    const step = Math.max(1, Math.floor(filtered.length / maxPoints));
    const positions = new Float32Array(Math.ceil(filtered.length / step) * 3);
    const lift = opts.liftZ != null ? opts.liftZ : 0;
    let n = 0;
    for (let i = 0; i < filtered.length; i += step) {
      const p = filtered[i];
      positions[n * 3] = p.x;
      positions[n * 3 + 1] = p.y;
      positions[n * 3 + 2] = (Number.isFinite(p.z) ? p.z : 0) + lift;
      n += 1;
    }
    const geo = new THREE.BufferGeometry();
    geo.setAttribute('position', new THREE.BufferAttribute(positions.slice(0, n * 3), 3));
    const sizePx = opts.size != null ? opts.size : 3;
    this.layer[key] = new THREE.Points(geo, new THREE.PointsMaterial({
      color: opts.color != null ? opts.color : 0x78a0c8,
      size: sizePx,
      sizeAttenuation: false,
    }));
    this.scene.add(this.layer[key]);
    this._applyLayerVisible(key);
  }

  setLayerVisible(layerKey, visible) {
    const map = {
      grid: 'grid',
      grid_map: 'grid',
      laser: 'laser',
      laser_scan: 'laser',
      localCloud: 'localCloud',
      local_cloud: 'localCloud',
      globalCloud: 'globalCloud',
      global_cloud: 'globalCloud',
      staticMap: 'pcd',
      static_map: 'pcd',
      path: 'path',
      navigationPath: 'path',
      navigation_path: 'path',
      collisionRobot: 'collisionRobot',
      collision_pose: 'collisionRobot',
      collision_robot_state: 'collisionRobot',
      attachObjects: 'attachObjects',
      attach_objects: 'attachObjects',
      attach_object_on_robot: 'attachObjects',
    };
    const key = map[layerKey] || layerKey;
    if (!this.layerVisible) this.layerVisible = {};
    this.layerVisible[key] = !!visible;
    this._applyLayerVisible(key);
  }

  _applyLayerVisible(key) {
    if (key === 'collisionRobot') {
      this._syncCollisionGhostVisible();
      return;
    }
    if (key === 'attachObjects') {
      this._setAttachedObjectsVisible(this.layerVisible?.attachObjects !== false);
      return;
    }
    const obj = this.layer?.[key];
    if (!obj) return;
    const show = this.layerVisible?.[key];
    obj.visible = show !== false;
  }

  /** Render collision objects managed by Motion, independently of a plan. */
  setMotionObstacles(obstacles = [], selectedId = null) {
    const group = this.layer.motionObstacles;
    while (group.children.length) {
      const child = group.children.pop();
      child.traverse((item) => {
        item.geometry?.dispose?.();
        if (Array.isArray(item.material)) item.material.forEach((m) => m.dispose?.());
        else item.material?.dispose?.();
      });
    }
    const colors = { box: 0x22b8cf, sphere: 0xb26be8, cylinder: 0x72c472 };
    obstacles.filter((item) => !item.external).forEach((obstacle) => {
      const scale = obstacle.scale || [1, 1, 1];
      const pose = obstacle.pose || [0, 0, 0, 0, 0, 0, 1];
      const selected = obstacle.obstacle_id === selectedId;
      let geometry;
      if (obstacle.obstacle_type === 'sphere') geometry = new THREE.SphereGeometry(0.5, 24, 16);
      else if (obstacle.obstacle_type === 'cylinder') geometry = new THREE.CylinderGeometry(0.5, 0.5, 1, 24);
      else geometry = new THREE.BoxGeometry(1, 1, 1);
      const color = selected ? 0xffcc00 : (colors[obstacle.obstacle_type] || colors.box);
      const mesh = new THREE.Mesh(geometry, new THREE.MeshPhongMaterial({ color, transparent: true, opacity: selected ? 0.52 : 0.30, side: THREE.DoubleSide }));
      mesh.userData.motionObstacle = obstacle;
      mesh.userData.editableMotionObstacle = obstacle.__draft === true;
      mesh.position.set(Number(pose[0]) || 0, Number(pose[1]) || 0, Number(pose[2]) || 0);
      mesh.quaternion.set(Number(pose[3]) || 0, Number(pose[4]) || 0, Number(pose[5]) || 0, Number(pose[6]) || 1);
      if (obstacle.obstacle_type === 'sphere') {
        const diameter = (Number(scale[0]) || 0.01) * 2;
        mesh.scale.set(diameter, diameter, diameter);
      } else if (obstacle.obstacle_type === 'cylinder') {
        const diameter = (Number(scale[0]) || 0.01) * 2;
        mesh.scale.set(diameter, Number(scale[1]) || 0.01, diameter);
      } else mesh.scale.set(Number(scale[0]) || 1, Number(scale[1]) || 1, Number(scale[2]) || 1);
      const edges = new THREE.LineSegments(new THREE.EdgesGeometry(geometry), new THREE.LineBasicMaterial({ color }));
      mesh.add(edges);
      group.add(mesh);
    });
    this._motionDraft = obstacles.find((item) => item.__draft) || null;
    this._renderMotionObstacleGizmo();
  }

  setMotionObstacleVisible(visible) {
    this.layer.motionObstacles.visible = !!visible;
  }

  enableMotionObstacleEditing(callback) {
    this.onMotionObstacleEdit = callback;
  }

  setMotionObstacleEditingEnabled(enabled) {
    this.motionObstacleEditingEnabled = !!enabled;
    this.canvas.style.cursor = '';
    this._renderMotionObstacleGizmo();
  }

  _renderMotionObstacleGizmo() {
    const group = this.layer.motionObstacleGizmo;
    while (group.children.length) this._remove(group.children.pop());
    const obstacle = this._motionDraft;
    if (!this.motionObstacleEditingEnabled || !obstacle) return;
    const origin = new THREE.Vector3(...obstacle.pose.slice(0, 3));
    const handle = (axis, distance, color, kind) => {
      const direction = new THREE.Vector3(...axis);
      group.add(new THREE.ArrowHelper(direction, origin, Math.max(0.18, distance), color, 0.07, 0.04));
      const dot = new THREE.Mesh(new THREE.SphereGeometry(0.055, 14, 10), new THREE.MeshBasicMaterial({ color, depthTest: false }));
      dot.position.copy(origin).addScaledVector(direction, Math.max(0.18, distance));
      dot.userData.motionGizmo = { axis, kind };
      group.add(dot);
    };
    // A conventional XYZ translation triad starts at the body centre. Its
    // spherical arrow tips are distinct from the resize handles at the edge.
    handle([1, 0, 0], 0.26, 0xff3b30, 'move-x');
    handle([0, 1, 0], 0.26, 0x34c759, 'move-y');
    handle([0, 0, 1], 0.26, 0x0a84ff, 'move-z');
    if (obstacle.obstacle_type === 'box') {
      handle([1, 0, 0], obstacle.scale[0] / 2 + 0.12, 0xff3b30, 'x');
      handle([0, 1, 0], obstacle.scale[1] / 2 + 0.12, 0x34c759, 'y');
      handle([0, 0, 1], obstacle.scale[2] / 2 + 0.12, 0x0a84ff, 'z');
    } else if (obstacle.obstacle_type === 'cylinder') {
      handle([1, 0, 0], obstacle.scale[0] + 0.12, 0xffcc00, 'radius');
      handle([0, 0, 1], obstacle.scale[1] / 2 + 0.12, 0x0a84ff, 'height');
    } else handle([1, 0, 0], obstacle.scale[0] + 0.12, 0xffcc00, 'radius');
  }

  _worldAtZ(event, z) {
    const point = this._eventPos(event);
    this._ndc.x = (point.x / point.w) * 2 - 1;
    this._ndc.y = -(point.y / point.h) * 2 + 1;
    this._raycaster.setFromCamera(this._ndc, this.camera);
    const plane = new THREE.Plane(new THREE.Vector3(0, 0, 1), -z);
    const hit = new THREE.Vector3();
    return this._raycaster.ray.intersectPlane(plane, hit) ? hit : null;
  }

  setMotionSelectionPoints(points = []) {
    const group = this.layer.motionPointSelection;
    while (group.children.length) this._remove(group.children.pop());
    if (!points.length) return;
    // Pixel-size highlights stay small even when the camera is close.
    const positions = new Float32Array(points.length * 3);
    points.forEach((point, index) => {
      positions[index * 3] = point.x;
      positions[index * 3 + 1] = point.y;
      positions[index * 3 + 2] = point.z;
    });
    const geometry = new THREE.BufferGeometry();
    geometry.setAttribute('position', new THREE.BufferAttribute(positions, 3));
    const marker = new THREE.Points(geometry, new THREE.ShaderMaterial({
      transparent: true,
      depthTest: false,
      depthWrite: false,
      vertexShader: 'void main() { gl_PointSize = 8.0; gl_Position = projectionMatrix * modelViewMatrix * vec4(position, 1.0); }',
      fragmentShader: 'void main() { vec2 p = gl_PointCoord - vec2(0.5); float d = length(p); if (d > 0.5) discard; float edge = smoothstep(0.5, 0.34, d); gl_FragColor = vec4(1.0, 0.80, 0.0, edge); }',
    }));
    marker.renderOrder = 2;
    group.add(marker);
  }

  setNavigationWaypoints(points = []) {
    const group = this.layer.navigationWaypoints;
    while (group.children.length) this._remove(group.children.pop());
    if (!points.length) return;
    if (points.length > 1) {
      const geometry = new THREE.BufferGeometry().setFromPoints(points.map((point) => new THREE.Vector3(point.x, point.y, 0.055)));
      group.add(new THREE.Line(geometry, new THREE.LineBasicMaterial({ color: 0x72bdff, transparent: true, opacity: 0.8 })));
    }
    points.forEach((point, index) => {
      const marker = new THREE.Mesh(
        new THREE.CircleGeometry(0.11, 24),
        new THREE.MeshBasicMaterial({ color: 0x4a9eff, side: THREE.DoubleSide, depthTest: true }),
      );
      marker.position.set(point.x, point.y, 0.06);
      group.add(marker);
      group.add(this._makeLabel(String(index + 1), point.x, point.y, 0.26, '#dceaff', 0.7));
    });
  }

  _cloudOpts(kind) {
    const cfg = this.cloudView || {};
    const size = kind === 'local'
      ? (Number.isFinite(cfg.localPointSizePx) ? cfg.localPointSizePx : (cfg.pointSizePx || 3))
      : (Number.isFinite(cfg.globalPointSizePx) ? cfg.globalPointSizePx : (cfg.pointSizePx || 3));
    if (kind === 'local') {
      return {
        color: 0x4ecdc4,
        size,
        maxPoints: Number.isFinite(cfg.localMaxPoints) ? Math.min(8000, Math.max(1000, cfg.localMaxPoints)) : 8000,
        minZ: cfg.localMinZ ?? -Infinity,
        maxZ: cfg.localMaxZ ?? Infinity,
        crop: cfg.localCrop ? { ...this._localCloudCropCenter(), ...cfg.localCrop } : undefined,
      };
    }
    return {
      color: 0xf4a261,
      size,
      maxPoints: 200000,
      minZ: Number.isFinite(cfg.globalMinZ) ? cfg.globalMinZ : 0,
      maxZ: Number.isFinite(cfg.globalMaxZ) ? cfg.globalMaxZ : 2,
    };
  }

  setCloudViewOptions( partial = {} ) {
    this.cloudView = {
      localMinZ: -Infinity,
      localMaxZ: Infinity,
      localMaxPoints: 8000,
      localCrop: null,
      globalMinZ: 0,
      globalMaxZ: 2,
      pointSizePx: 3,
      staticPointSizePx: 2,
      localPointSizePx: 3,
      ...(this.cloudView || {}),
      ...partial,
    };
    if (this.layer.pcd?.material && Number.isFinite(this.cloudView.staticPointSizePx)) {
      this.layer.pcd.material.size = this.cloudView.staticPointSizePx;
      this.layer.pcd.material.needsUpdate = true;
    }
    if (this._localCloudRaw) this.setLocalCloud(this._localCloudRaw, { keepRaw: true });
    if (this._globalCloudRaw) this.setGlobalCloud(this._globalCloudRaw, { keepRaw: true });
  }

  pickCloudPoint(event, { pixelRadius = 8 } = {}) {
    const p = this._eventPos(event);
    this._ndc.x = (p.x / p.w) * 2 - 1;
    this._ndc.y = -(p.y / p.h) * 2 + 1;
    this._raycaster.setFromCamera(this._ndc, this.camera);
    const height = Math.max(1, p.h);
    let worldPerPixel;
    if (this.camera.isOrthographicCamera) {
      worldPerPixel = Math.abs(this.camera.top - this.camera.bottom) / height;
    } else {
      const distance = this.camera.position.distanceTo(this.controls.target);
      worldPerPixel = 2 * Math.tan(THREE.MathUtils.degToRad(this.camera.fov) / 2) * distance / height;
    }
    this._raycaster.params.Points.threshold = Math.max(0.01, worldPerPixel * pixelRadius);
    const clouds = ['pcd', 'localCloud', 'globalCloud']
      .map((key) => this.layer[key])
      .filter((object) => object?.visible && object.isPoints);
    const hit = this._raycaster.intersectObjects(clouds, false)[0];
    if (!hit || !Number.isInteger(hit.index)) return null;
    const position = hit.object.geometry?.getAttribute('position');
    if (!position || hit.index >= position.count) return null;
    const point = new THREE.Vector3().fromBufferAttribute(position, hit.index);
    hit.object.localToWorld(point);
    return { x: point.x, y: point.y, z: point.z, source: 'cloud' };
  }

  setMeasurement(pointA, pointB, options = {}) {
    const group = this.layer.measure;
    while (group.children.length) {
      const child = group.children.pop();
      child.geometry?.dispose?.();
      if (child.material) {
        child.material.map?.dispose?.();
        child.material.dispose?.();
      }
    }
    const addAnchor = (point, color) => {
      if (!point) return;
      const marker = new THREE.Mesh(
        new THREE.SphereGeometry(0.09, 18, 12),
        new THREE.MeshBasicMaterial({ color, depthTest: false }),
      );
      marker.position.set(point.x, point.y, point.z);
      marker.renderOrder = 30;
      group.add(marker);
    };
    addAnchor(pointA, options.lockA ? 0x9aa0a6 : 0x4a9eff);
    addAnchor(pointB, options.lockB ? 0x9aa0a6 : 0xff6b9d);
    if (!pointA || !pointB) return;
    const a = new THREE.Vector3(pointA.x, pointA.y, pointA.z);
    const b = new THREE.Vector3(pointB.x, pointB.y, pointB.z);
    const line = new THREE.Line(
      new THREE.BufferGeometry().setFromPoints([a, b]),
      new THREE.LineBasicMaterial({ color: 0xffd166, depthTest: false }),
    );
    line.renderOrder = 29;
    group.add(line);
    const dx = pointB.x - pointA.x;
    const dy = pointB.y - pointA.y;
    const dz = pointB.z - pointA.z;
    const distance = Math.hypot(dx, dy, dz);
    const label = this._makeLabel(
      `${distance.toFixed(3)} m`,
      (pointA.x + pointB.x) / 2,
      (pointA.y + pointB.y) / 2,
      (pointA.z + pointB.z) / 2 + 0.16,
      '#ffd166',
      0.9,
    );
    label.material.depthTest = false;
    label.renderOrder = 31;
    group.add(label);
  }

  setMeasurementCursor(point, { snapped = false } = {}) {
    const group = this.layer.measureCursor;
    while (group.children.length) {
      const child = group.children.pop();
      child.geometry?.dispose?.();
      child.material?.dispose?.();
    }
    if (!point || !snapped) return;
    const color = point.source === 'waypoint' ? 0xff6b6b : 0x4ecdc4;
    const marker = new THREE.Mesh(
      new THREE.SphereGeometry(0.075, 16, 10),
      new THREE.MeshBasicMaterial({ color, depthTest: false }),
    );
    marker.position.set(point.x, point.y, point.z);
    marker.renderOrder = 40;
    group.add(marker);
    const span = 0.18;
    const cross = new THREE.LineSegments(
      new THREE.BufferGeometry().setFromPoints([
        new THREE.Vector3(point.x - span, point.y, point.z),
        new THREE.Vector3(point.x + span, point.y, point.z),
        new THREE.Vector3(point.x, point.y - span, point.z),
        new THREE.Vector3(point.x, point.y + span, point.z),
        new THREE.Vector3(point.x, point.y, point.z - span),
        new THREE.Vector3(point.x, point.y, point.z + span),
      ]),
      new THREE.LineBasicMaterial({ color, depthTest: false }),
    );
    cross.renderOrder = 39;
    group.add(cross);
  }

  setLocalCloud(cloud, opts = {}) {
    if (!opts.keepRaw) this._localCloudRaw = cloud || null;
    const src = opts.keepRaw ? this._localCloudRaw : cloud;
    const cloudOptions = this._cloudOpts('local');
    if (cloudOptions.crop) Object.assign(cloudOptions.crop, this._localCloudCropCenter(src));
    this.setCloudPoints('localCloud', src?.points || [], cloudOptions);
  }

  _localCloudCropCenter(cloud = this._localCloudRaw) {
    const frame = String(cloud?.frame_id || '').toLowerCase().replace(/^\//, '');
    if (['map', 'odom', 'world'].includes(frame)) {
      return { centerX: this.robot?.x || 0, centerY: this.robot?.y || 0, centerZ: this.robot?.z || 0 };
    }
    return { centerX: 0, centerY: 0, centerZ: 0 };
  }

  clearLocalCloud() {
    this._localCloudRaw = null;
    this._remove(this.layer.localCloud);
    this.layer.localCloud = null;
  }

  setGlobalCloud(cloud, opts = {}) {
    if (!opts.keepRaw) this._globalCloudRaw = cloud || null;
    const src = opts.keepRaw ? this._globalCloudRaw : cloud;
    this.setCloudPoints('globalCloud', src?.points || [], this._cloudOpts('global'));
    if (opts.fit && src?.points?.length) {
      let minX = Infinity, maxX = -Infinity, minY = Infinity, maxY = -Infinity;
      for (const point of src.points) {
        if (!Number.isFinite(point.x) || !Number.isFinite(point.y)) continue;
        minX = Math.min(minX, point.x); maxX = Math.max(maxX, point.x);
        minY = Math.min(minY, point.y); maxY = Math.max(maxY, point.y);
      }
      if (Number.isFinite(minX)) {
        this.bounds = { minX: minX - 1, maxX: maxX + 1, minY: minY - 1, maxY: maxY + 1 };
        this.fitToBounds();
      }
    }
  }

  clearGlobalCloud() {
    this._globalCloudRaw = null;
    this._remove(this.layer.globalCloud);
    this.layer.globalCloud = null;
  }

  /** Resolve edge polyline: prefer exported geometry.points, else sample curve. */
  _edgePathPoints(edge, fromNode, toNode) {
    const pts = edge?.geometry?.points;
    if (Array.isArray(pts) && pts.length >= 2) {
      return pts.map((p) => ({ x: Number(p.x) || 0, y: Number(p.y) || 0 }));
    }
    if (fromNode?.pose && toNode?.pose) {
      try {
        return sampleEdgePath(edge, fromNode, toNode);
      } catch (err) {
        console.warn('sampleEdgePath failed', edge?.id, err);
      }
      return [
        { x: fromNode.pose.x, y: fromNode.pose.y },
        { x: toNode.pose.x, y: toNode.pose.y },
      ];
    }
    return [];
  }

  setTopo(topo, { fit = false } = {}) {
    this.topo = topo;
    while (this.layer.topo.children.length) {
      const c = this.layer.topo.children.pop();
      c.geometry?.dispose?.();
      if (c.material) {
        c.material.map?.dispose?.();
        c.material.dispose?.();
      }
    }
    if (!topo?.nodes?.length) return;
    const nodes = Object.fromEntries(topo.nodes.map((n) => [n.id, n]));
    let minX = Infinity, maxX = -Infinity, minY = Infinity, maxY = -Infinity;
    const expand = (x, y) => {
      minX = Math.min(minX, x); maxX = Math.max(maxX, x);
      minY = Math.min(minY, y); maxY = Math.max(maxY, y);
    };
    for (const e of topo.edges || []) {
      const a = nodes[e.from], b = nodes[e.to];
      if (!a || !b) continue;
      const pathPts = this._edgePathPoints(e, a, b);
      if (pathPts.length < 2) continue;
      const vectors = pathPts.map((p) => {
        expand(p.x, p.y);
        return new THREE.Vector3(p.x, p.y, 0.05);
      });
      const geo = new THREE.BufferGeometry().setFromPoints(vectors);
      this.layer.topo.add(new THREE.Line(geo, new THREE.LineBasicMaterial({ color: 0xf0c040 })));
    }
    for (const n of topo.nodes) {
      const x = n.pose?.x ?? 0, y = n.pose?.y ?? 0;
      const yaw = Number(n.pose?.yaw) || 0;
      const hasPoseDecoration = n.radius != null || n.tags?.radius != null;
      const radius = Math.max(0.05, Number(n.radius ?? n.tags?.radius) || 0.35);
      const hitRadius = Math.max(radius, Number(n.tags?.hit_radius) || 0);
      const markerSize = Math.max(0.03, Math.min(1, Number(n.tags?.marker_size) || 0.12));
      const arrowScale = Math.max(0.2, Math.min(5, Number(n.tags?.arrow_scale) || 1));
      const showRadiusRing = n.tags?.show_radius_ring !== false;
      const showLabel = n.tags?.show_label !== false;
      const labelScale = Math.max(0.3, Math.min(3, Number(n.tags?.label_scale) || 1));
      const labelPosition = ['above', 'center', 'below'].includes(n.tags?.label_position)
        ? n.tags.label_position : 'center';
      const selected = n.id === this.selectedNodeId;
      const locked = n.tags?.locked === true;
      const multiSelected = n.tags?.multi_selected === true;
      const markerColor = selected
        ? (locked ? 0x8f1d2c : 0xff6b6b)
        : (multiSelected ? 0x8d6cff : (locked ? 0x7b818c : (hasPoseDecoration ? 0xe9b949 : 0x4a9eff)));
      if (hasPoseDecoration) {
        expand(x - radius, y - radius);
        expand(x + radius, y + radius);
        // Invisible pick surface: the complete chassis footprint remains
        // selectable even when its visual ring is hidden.
        const hitArea = new THREE.Mesh(
          new THREE.CircleGeometry(hitRadius, 64),
          new THREE.MeshBasicMaterial({
            transparent: true,
            opacity: 0,
            colorWrite: false,
            depthWrite: false,
            depthTest: false,
            side: THREE.DoubleSide,
          }),
        );
        hitArea.position.set(x, y, 0.02);
        hitArea.userData.node = n;
        hitArea.userData.waypointHitArea = true;
        this.layer.topo.add(hitArea);

        if (showRadiusRing) {
          const ring = new THREE.Mesh(
            new THREE.RingGeometry(Math.max(0.01, radius - 0.025), radius + 0.025, 64),
            new THREE.MeshBasicMaterial({
              color: markerColor,
              transparent: true,
              opacity: selected ? 0.95 : 0.72,
              side: THREE.DoubleSide,
              depthTest: true,
            }),
          );
          ring.position.set(x, y, 0.035);
          ring.userData.node = n;
          this.layer.topo.add(ring);
        }

        const headingLength = Math.max(0.5, radius * 1.25) * arrowScale;
        const ex = x + Math.cos(yaw) * headingLength;
        const ey = y + Math.sin(yaw) * headingLength;
        const heading = new THREE.Line(
          new THREE.BufferGeometry().setFromPoints([
            new THREE.Vector3(x, y, 0.07),
            new THREE.Vector3(ex, ey, 0.07),
          ]),
          new THREE.LineBasicMaterial({ color: markerColor }),
        );
        heading.userData.node = n;
        this.layer.topo.add(heading);
        const back = 0.15 * arrowScale;
        const wing = 0.08 * arrowScale;
        const bx = ex - Math.cos(yaw) * back;
        const by = ey - Math.sin(yaw) * back;
        const px = -Math.sin(yaw) * wing;
        const py = Math.cos(yaw) * wing;
        const arrowGeometry = new THREE.BufferGeometry();
        arrowGeometry.setAttribute('position', new THREE.Float32BufferAttribute([
          ex, ey, 0.071,
          bx + px, by + py, 0.071,
          bx - px, by - py, 0.071,
        ], 3));
        arrowGeometry.setIndex([0, 1, 2]);
        arrowGeometry.computeVertexNormals();
        const arrow = new THREE.Mesh(
          arrowGeometry,
          new THREE.MeshBasicMaterial({ color: markerColor, side: THREE.DoubleSide }),
        );
        arrow.userData.node = n;
        this.layer.topo.add(arrow);
      } else {
        expand(x, y);
      }

      const mesh = new THREE.Mesh(
        new THREE.SphereGeometry(markerSize, 16, 12),
        new THREE.MeshPhongMaterial({ color: markerColor }),
      );
      mesh.position.set(x, y, 0.12);
      mesh.userData.node = n;
      this.layer.topo.add(mesh);
      const label = n.tags?.display_name || n.name || n.id;
      if (label && showLabel) {
        const labelClearance = Math.max(radius, markerSize) + 0.175 * labelScale + 0.06;
        const labelY = labelPosition === 'above' ? y + labelClearance
          : (labelPosition === 'below' ? y - labelClearance : y);
        const labelSprite = this._makeLabel(label, x, labelY, 0.35, '#e8ecf2', labelScale);
        labelSprite.userData.node = n;
        this.layer.topo.add(labelSprite);
      }
    }
    for (const poi of topo.pois || []) {
      const n = nodes[poi.node_id];
      if (!n) continue;
      const text = `POI:${poi.name || poi.id}`;
      this.layer.topo.add(this._makeLabel(text, n.pose.x, n.pose.y, 0.55, '#9ad0ff'));
    }
    if (!Number.isFinite(minX)) {
      minX = -5; maxX = 5; minY = -5; maxY = 5;
    }
    this.bounds = { minX: minX - 1.5, maxX: maxX + 1.5, minY: minY - 1.5, maxY: maxY + 1.5 };
    // Only reframe when caller explicitly asks (e.g. first load). Never on selection.
    if (fit) this._frameBounds();
  }

  _refreshTopoSelection() {
    for (const c of this.layer.topo.children) {
      if (!c.isMesh || !c.userData.node) continue;
      c.material.color.setHex(c.userData.node.id === this.selectedNodeId ? 0xff6b6b : 0x4a9eff);
    }
  }

  _makeLabel(text, x, y, z = 0.35, color = '#e8ecf2', scale = 1) {
    const c = document.createElement('canvas');
    const ctx = c.getContext('2d');
    const font = 'bold 28px sans-serif';
    ctx.font = font;
    const tw = Math.ceil(ctx.measureText(text).width) + 24;
    c.width = tw;
    c.height = 44;
    ctx.font = font;
    ctx.fillStyle = 'rgba(20,24,30,0.75)';
    ctx.fillRect(0, 0, tw, 44);
    ctx.fillStyle = color;
    ctx.textBaseline = 'middle';
    ctx.fillText(text, 12, 22);
    const tex = new THREE.CanvasTexture(c);
    const mat = new THREE.SpriteMaterial({ map: tex, transparent: true, depthTest: false });
    const spr = new THREE.Sprite(mat);
    spr.position.set(x, y, z);
    const safeScale = Math.max(0.3, Math.min(3, Number(scale) || 1));
    spr.scale.set(tw * 0.008 * safeScale, 0.35 * safeScale, 1);
    return spr;
  }

  setGoal(goal) {
    this.goal = goal;
    if (this.layer.goal) {
      this.scene.remove(this.layer.goal);
      this.layer.goal.traverse((c) => {
        c.geometry?.dispose?.();
        if (c.material) {
          if (Array.isArray(c.material)) c.material.forEach((m) => m.dispose?.());
          else c.material.dispose?.();
        }
      });
      this.layer.goal = null;
    }
    if (!goal) {
      this.onGoalChange(null);
      return;
    }
    const g = new THREE.Group();
    const markerColor = this.mode === 'initialPose' ? 0x35d07f : 0x4a9eff;
    const disk = new THREE.Mesh(
      new THREE.CircleGeometry(0.12, 28),
      new THREE.MeshBasicMaterial({ color: markerColor, side: THREE.DoubleSide, depthTest: true }),
    );
    disk.position.z = 0.04;
    g.add(disk);
    // 0.5 m heading arrow in local +X; group yaw aligns it in map frame.
    const arrow = new THREE.ArrowHelper(
      new THREE.Vector3(1, 0, 0),
      new THREE.Vector3(0, 0, 0.06),
      0.5,
      markerColor,
      0.14,
      0.09,
    );
    g.add(arrow);
    g.position.set(goal.x, goal.y, 0);
    g.rotation.z = goal.yaw || 0;
    this.layer.goal = g;
    this.scene.add(g);
    this.onGoalChange(goal);
  }

  setRobotPose(pose) {
    this.robot = pose;
    if (!pose) return;
    if (!this.robotMarker) {
      const g = new THREE.Group();
      const body = new THREE.Mesh(
        new THREE.ConeGeometry(0.18, 0.5, 16),
        new THREE.MeshPhongMaterial({ color: 0xff6b4a }),
      );
      body.rotation.x = Math.PI / 2;
      g.add(body);
      this.robotMarker = g;
      this.scene.add(g);
    }
    this.robotMarker.position.set(pose.x, pose.y, 0.25);
    this.robotMarker.rotation.z = pose.yaw || 0;
    this.robotMarker.visible = this.robotVisible !== false && !this.urdfModel;
    this._setFramePose(this.coordinateFrames.base, [
      pose.x, pose.y, pose.z || 0,
      pose.qx || 0, pose.qy || 0, pose.qz || 0, pose.qw ?? 1,
    ]);

    if (this.urdfModel) {
      if (pose.qx != null) {
        this.urdfModel.setBasePoseQuaternion(
          pose.x, pose.y, pose.z || 0, pose.qx, pose.qy, pose.qz, pose.qw ?? 1,
        );
      } else {
        this.urdfModel.setBasePose(pose.x, pose.y, pose.z || 0, 0, 0, pose.yaw || 0);
      }
      this.urdfModel.root.visible = this.robotVisible !== false;
    }
    this._refreshStaticCloudCrop();
    if (this.followRobot) {
      const t = this.controls.target;
      const dx = pose.x - t.x, dy = pose.y - t.y;
      if (Math.hypot(dx, dy) > 3) {
        this.controls.target.set(pose.x, pose.y, 0);
        this.camera.position.x += dx;
        this.camera.position.y += dy;
      }
    }
  }

  setRobotVisible(visible) {
    this.robotVisible = !!visible;
    if (this.urdfModel?.root) this.urdfModel.root.visible = this.robotVisible;
    if (this.robotMarker) this.robotMarker.visible = this.robotVisible && !this.urdfModel;
  }

  setGroundGridVisible(visible) {
    this.groundGrid.visible = !!visible;
  }

  setRobotTint(color = 0x35d07f, opacity = 0.9) {
    this._tintUrdfGhost(this.urdfModel, color, opacity);
  }

  setPreviewRobotTint(color = 0xffa726, opacity = 0.48) {
    this._tintUrdfGhost(this.collisionUrdfModel, color, opacity);
  }

  setPreviewRobotPose(pose) {
    if (!pose) {
      this.clearCollisionRobotState();
      return;
    }
    this.setCollisionRobotState({ pose });
  }

  setPreviewRobotState(pose, joints = null) {
    if (!pose) {
      this.clearCollisionRobotState();
      return;
    }
    this.setCollisionRobotState({
      pose,
      names: joints?.names || [],
      position: joints?.position || [],
    });
  }

  setJointState(js) {
    if (!this.urdfModel || !js?.position?.length) return;
    if (js.names?.length) {
      for (let i = 0; i < js.names.length; i++) {
        this.urdfModel.setJointValue(js.names[i], js.position[i] || 0);
      }
      return;
    }
    // Prefer robot_model.toml body joint order (same as collision_viewer).
    const order = (this.bodyJointNames?.length
      ? this.bodyJointNames
      : this.urdfModel.getMovableJoints().map((j) => j.name));
    const n = Math.min(order.length, js.position.length);
    for (let i = 0; i < n; i++) this.urdfModel.setJointValue(order[i], js.position[i] || 0);
  }

  getJointState(names) {
    if (!this.urdfModel || !Array.isArray(names)) return null;
    return {
      names: names.slice(),
      position: names.map((name) => Number(this.urdfModel.jointMap?.get(name)?.value) || 0),
    };
  }

  getMovableJointNames() {
    return this.urdfModel?.getMovableJoints().map((joint) => joint.name) || [];
  }

  getMovableJoints() {
    return (this.urdfModel?.getMovableJoints() || []).map(({ name, lower, upper, type }) => ({ name, lower, upper, type }));
  }

  _tintUrdfGhost(model, color = COLLISION_GHOST_COLOR, opacity = COLLISION_GHOST_OPACITY) {
    if (!model?.root) return;
    model.root.traverse((obj) => {
      if (!obj.isMesh || !obj.material) return;
      const mats = Array.isArray(obj.material) ? obj.material : [obj.material];
      for (let i = 0; i < mats.length; i++) {
        const src = mats[i];
        const m = src.clone ? src.clone() : src;
        if (m.color) m.color.setHex(color);
        m.transparent = true;
        m.opacity = opacity;
        m.depthWrite = false;
        m.needsUpdate = true;
        mats[i] = m;
      }
      obj.material = Array.isArray(obj.material) ? mats : mats[0];
    });
  }

  _setCollisionGhostOpacity(opacity) {
    const model = this.collisionUrdfModel;
    if (!model?.root) return;
    model.root.traverse((obj) => {
      if (!obj.isMesh || !obj.material) return;
      const mats = Array.isArray(obj.material) ? obj.material : [obj.material];
      for (const m of mats) {
        m.transparent = true;
        m.opacity = opacity;
        m.depthWrite = false;
      }
    });
  }

  setCollisionGhostFadeMs(ms) {
    const next = Number(ms);
    this._collisionGhostFadeMs = Number.isFinite(next) && next > 0 ? next : 0;
    if (this._collisionGhostAlive && this._collisionGhostFadeMs <= 0) {
      this._setCollisionGhostOpacity(this._collisionGhostBaseOpacity);
    }
  }

  restartCollisionGhostFade() {
    if (!this._collisionGhostAlive) return;
    this._collisionGhostLastMs = performance.now();
    this._setCollisionGhostOpacity(this._collisionGhostBaseOpacity);
  }

  _syncCollisionGhostVisible() {
    if (!this.collisionUrdfModel?.root) return;
    const layerOn = this.layerVisible?.collisionRobot !== false;
    this.collisionUrdfModel.root.visible = layerOn && this._collisionGhostAlive;
  }

  _updateCollisionGhostFade() {
    if (!this._collisionGhostAlive || this._collisionGhostFadeMs <= 0) return;
    const k = 1 - (performance.now() - this._collisionGhostLastMs) / this._collisionGhostFadeMs;
    if (k <= 0) {
      this.clearCollisionRobotState();
      try { this.onCollisionGhostTimeout?.(); } catch (_) {}
      return;
    }
    this._setCollisionGhostOpacity(this._collisionGhostBaseOpacity * Math.min(1, Math.max(0, k)));
  }

  /** Show collision whole-body pose as a red translucent URDF ghost. */
  setCollisionRobotState(state) {
    if (!state?.pose && !state?.position?.length) return;
    if (!this.collisionUrdfModel) return;
    const pose = state.pose || {};
    if (pose.qx != null) {
      this.collisionUrdfModel.setBasePoseQuaternion(
        pose.x || 0, pose.y || 0, pose.z || 0,
        pose.qx, pose.qy, pose.qz, pose.qw ?? 1,
      );
    } else {
      this.collisionUrdfModel.setBasePose(
        pose.x || 0, pose.y || 0, pose.z || 0, 0, 0, pose.yaw || 0,
      );
    }
    const positions = state.position || [];
    if (positions.length) {
      if (state.names?.length) {
        for (let i = 0; i < Math.min(state.names.length, positions.length); i++) {
          this.collisionUrdfModel.setJointValue(state.names[i], positions[i] || 0);
        }
      } else {
        const order = (this.bodyJointNames?.length
          ? this.bodyJointNames
          : this.collisionUrdfModel.getMovableJoints().map((j) => j.name));
        const n = Math.min(order.length, positions.length);
        for (let i = 0; i < n; i++) {
          this.collisionUrdfModel.setJointValue(order[i], positions[i] || 0);
        }
      }
    }
    this._collisionGhostAlive = true;
    this._collisionGhostLastMs = performance.now();
    this._setCollisionGhostOpacity(this._collisionGhostBaseOpacity);
    this._syncCollisionGhostVisible();
  }

  clearCollisionRobotState() {
    this._collisionGhostAlive = false;
    this._collisionGhostLastMs = 0;
    if (this.collisionUrdfModel?.root) this.collisionUrdfModel.root.visible = false;
  }

  setAttachUseSpheres(useSpheres) {
    this._attachUseSpheres = !!useSpheres;
    this._applyAttachedObjects();
  }

  setAttachedObjects(list, opts = {}) {
    this._attachObjectsRaw = Array.isArray(list) ? list.slice() : [];
    if (opts.useSpheres !== undefined) this._attachUseSpheres = !!opts.useSpheres;
    this._applyAttachedObjects();
  }

  clearAttachedObjects() {
    this._attachObjectsRaw = [];
    if (this.urdfModel?.clearAttachedObjects) this.urdfModel.clearAttachedObjects();
  }

  _setAttachedObjectsVisible(visible) {
    const attached = this.urdfModel?.attachedObjects;
    if (!attached) return;
    for (const rec of attached.values()) {
      if (rec?.group) rec.group.visible = !!visible;
    }
  }

  _applyAttachedObjects() {
    if (!this.urdfModel?.attachSimpObject) return;
    if (this.urdfModel.clearAttachedObjects) this.urdfModel.clearAttachedObjects();
    const useSpheres = this._attachUseSpheres !== false;
    const show = this.layerVisible?.attachObjects !== false;
    for (const item of this._attachObjectsRaw || []) {
      if (!item?.parentLink) continue;
      try {
        this.urdfModel.attachSimpObject({
          ...item,
          useSpheres,
          color: 0xff9f1c,
          opacity: 0.55,
        });
      } catch (e) {
        console.warn('[attach_object]', item?.name || '', e?.message || e);
      }
    }
    this._setAttachedObjectsVisible(show);
  }

  setBodyJointNames(names) {
    this.bodyJointNames = Array.isArray(names) ? names.slice() : [];
  }

  setGridMap(grid) {
    this._remove(this.layer.grid);
    this.layer.grid = null;
    if (!grid) return;
    const { width, height, resolution, origin, data } = grid;
    if (!width || !height || !resolution || !data) return;
    const need = width * height;
    if (data.length < need) {
      console.warn(`[grid_map] data length ${data.length} < ${width}x${height}=${need}`);
    }

    // DataTexture + Plane：与 RViz OccupancyGrid 一致（row-major，(0,0)=origin）
    // DataTexture 默认 flipY=false → 缓冲首行在纹理底边 = Plane V=0 = 局部 -Y = origin.y
    const rgba = new Uint8Array(need * 4);
    const n = Math.min(need, data.length);
    for (let i = 0; i < n; i++) {
      const occ = data[i] & 0xff;
      const o = i * 4;
      if (occ === 255) {
        rgba[o] = 70; rgba[o + 1] = 74; rgba[o + 2] = 82; rgba[o + 3] = 200;
      } else if (occ >= 50) {
        rgba[o] = 28; rgba[o + 1] = 28; rgba[o + 2] = 28; rgba[o + 3] = 240;
      } else {
        rgba[o] = 210; rgba[o + 1] = 215; rgba[o + 2] = 220; rgba[o + 3] = 120;
      }
    }
    const tex = new THREE.DataTexture(rgba, width, height, THREE.RGBAFormat);
    tex.magFilter = THREE.NearestFilter;
    tex.minFilter = THREE.NearestFilter;
    tex.flipY = false;
    tex.needsUpdate = true;

    const w = width * resolution;
    const h = height * resolution;
    const yaw = origin.yaw || 0;
    const mesh = new THREE.Mesh(
      new THREE.PlaneGeometry(w, h),
      new THREE.MeshBasicMaterial({ map: tex, transparent: true, depthWrite: false }),
    );
    const cx = w / 2;
    const cy = h / 2;
    const cya = Math.cos(yaw);
    const sya = Math.sin(yaw);
    mesh.position.set(
      origin.x + cya * cx - sya * cy,
      origin.y + sya * cx + cya * cy,
      0.01,
    );
    mesh.rotation.z = yaw;
    this.layer.grid = mesh;
    this.scene.add(mesh);
    this._applyLayerVisible('grid');
    this.bounds = {
      minX: Math.min(this.bounds.minX, origin.x - 1),
      maxX: Math.max(this.bounds.maxX, origin.x + w + 1),
      minY: Math.min(this.bounds.minY, origin.y - 1),
      maxY: Math.max(this.bounds.maxY, origin.y + h + 1),
    };
  }

  setLaserScan(scan, robotPose) {
    this._remove(this.layer.laser);
    this.layer.laser = null;
    if (!scan?.ranges?.length) return;
    // Transform a base_link laser scan into the map frame using the base pose.
    const base = robotPose || this.robot || { x: 0, y: 0, yaw: 0 };
    const cy = Math.cos(base.yaw || 0);
    const sy = Math.sin(base.yaw || 0);
    const pts = [];
    const amin = Number(scan.angleMin) || 0;
    const ainc = Number(scan.angleInc) || 0;
    const rmin = Number(scan.rangeMin) || 0;
    const rmax = Number(scan.rangeMax) || 100;
    for (let i = 0; i < scan.ranges.length; i++) {
      const range = scan.ranges[i];
      if (!Number.isFinite(range) || range < rmin || range > rmax) continue;
      const a = amin + i * ainc;
      const lx = range * Math.cos(a);
      const ly = range * Math.sin(a);
      pts.push(base.x + cy * lx - sy * ly, base.y + sy * lx + cy * ly, 0.08);
    }
    if (!pts.length) return;
    const geo = new THREE.BufferGeometry();
    geo.setAttribute('position', new THREE.Float32BufferAttribute(pts, 3));
    this.layer.laser = new THREE.Points(geo, new THREE.PointsMaterial({
      color: 0xff5050,
      size: 3,
      sizeAttenuation: false,
    }));
    this.scene.add(this.layer.laser);
    this._applyLayerVisible('laser');
  }

  setPath(poses, opts = {}) {
    if (!opts.keepRaw) this._pathRaw = Array.isArray(poses) ? poses : null;
    const src = opts.keepRaw ? this._pathRaw : poses;
    this._remove(this.layer.path);
    this.layer.path = null;
    if (!src?.length) return;
    const positions = [];
    for (const p of src) {
      positions.push(p.x, p.y, 0.06);
    }
    if (positions.length < 6) return;
    const geo = new LineGeometry();
    geo.setPositions(positions);
    const w = Math.max(1, this.canvas.clientWidth);
    const h = Math.max(1, this.canvas.clientHeight);
    const linewidth = Number.isFinite(this.pathWidthPx) ? this.pathWidthPx : 4;
    const mat = new LineMaterial({
      color: 0x5dff9a,
      linewidth,
      worldUnits: false,
      transparent: true,
      depthTest: true,
    });
    mat.resolution.set(w, h);
    this.layer.path = new Line2(geo, mat);
    this.layer.path.computeLineDistances();
    this.scene.add(this.layer.path);
    this._applyLayerVisible('path');
  }

  setPathWidth(px) {
    const n = Math.min(20, Math.max(1, Number(px) || 4));
    this.pathWidthPx = n;
    if (this.layer.path?.material) {
      this.layer.path.material.linewidth = n;
      this.layer.path.material.needsUpdate = true;
      return;
    }
    if (this._pathRaw) this.setPath(this._pathRaw, { keepRaw: true });
  }

  clearPath() {
    this._pathRaw = null;
    this._remove(this.layer.path);
    this.layer.path = null;
  }

  /** Draw a lightweight TCP path from planned joint samples. */
  setEndEffectorTrajectory(jointNames, frames, linkName) {
    this.clearEndEffectorTrajectory();
    const link = this.urdfModel?.linkGroups?.get(linkName);
    if (!link || !Array.isArray(jointNames) || !Array.isArray(frames) || frames.length < 2) return false;
    const original = this.getJointState(jointNames);
    const point = new THREE.Vector3();
    const points = [];
    const stride = Math.max(1, Math.ceil(frames.length / 500));
    try {
      for (let index = 0; index < frames.length; index += stride) {
        this.setJointState({ names: jointNames, position: frames[index] });
        this.urdfModel.root.updateMatrixWorld(true);
        link.getWorldPosition(point);
        points.push(point.clone());
      }
      const last = frames.length - 1;
      if ((last % stride) !== 0) {
        this.setJointState({ names: jointNames, position: frames[last] });
        this.urdfModel.root.updateMatrixWorld(true);
        link.getWorldPosition(point);
        points.push(point.clone());
      }
    } finally {
      if (original) this.setJointState(original);
      this.urdfModel.root.updateMatrixWorld(true);
    }
    if (points.length < 2) return false;
    const geometry = new LineGeometry();
    geometry.setPositions(points.flatMap((p) => [p.x, p.y, p.z]));
    const material = new LineMaterial({ color: 0xffc857, linewidth: 3, worldUnits: false, transparent: true, opacity: 0.9, depthTest: true });
    material.resolution.set(Math.max(1, this.canvas.clientWidth), Math.max(1, this.canvas.clientHeight));
    const line = new Line2(geometry, material);
    line.computeLineDistances();
    // Use a small wireframe TCP marker instead of a filled sphere.  The
    // marker is only attached while playback/frame inspection is active.
    const cursor = new THREE.Group();
    const markerLength = 0.12;
    [
      [new THREE.Vector3(0, 0, 0), new THREE.Vector3(markerLength, 0, 0), 0xff3b30],
      [new THREE.Vector3(0, 0, 0), new THREE.Vector3(0, markerLength, 0), 0x34c759],
      [new THREE.Vector3(0, 0, 0), new THREE.Vector3(0, 0, markerLength), 0x0a84ff],
    ].forEach(([start, end, color]) => {
      const line = new THREE.Line(
        new THREE.BufferGeometry().setFromPoints([start, end]),
        new THREE.LineBasicMaterial({ color, depthTest: false }),
      );
      line.renderOrder = 60;
      cursor.add(line);
    });
    cursor.position.copy(points[0]);
    const group = new THREE.Group();
    group.add(line, cursor);
    this.layer.motionTrajectory = { group, line, cursor, points };
    this.scene.add(group);
    return true;
  }

  setEndEffectorTrajectoryCursor(frameIndex, totalFrames) {
    const trajectory = this.layer.motionTrajectory;
    if (!trajectory) return;
    const ratio = totalFrames > 1 ? Math.min(1, Math.max(0, frameIndex / (totalFrames - 1))) : 0;
    const index = Math.round(ratio * (trajectory.points.length - 1));
    trajectory.cursor.position.copy(trajectory.points[index]);
  }

  clearEndEffectorTrajectory() {
    const trajectory = this.layer.motionTrajectory;
    if (!trajectory) return;
    this.scene.remove(trajectory.group);
    trajectory.line.geometry.dispose();
    trajectory.line.material.dispose();
    trajectory.cursor.traverse((child) => {
      child.geometry?.dispose?.();
      child.material?.dispose?.();
    });
    this.layer.motionTrajectory = null;
  }

  async loadUrdfText(text, name = 'robot.urdf', options = {}) {
    if (this.urdfModel) {
      this.scene.remove(this.urdfModel.root);
      this.urdfModel = null;
    }
    if (this.collisionUrdfModel) {
      this.scene.remove(this.collisionUrdfModel.root);
      this.collisionUrdfModel = null;
    }
    this._collisionGhostAlive = false;
    this._collisionGhostLastMs = 0;
    this.urdfSourceText = text;
    this.urdfSourceName = name;
    this.urdfDisplayMode = options.displayMode === 'visual' ? 'visual' : 'collision';
    this.urdfLoadOptions = options;
    this.urdfModel = await UrdfModel.loadFromText(text, name, { ...options, displayMode: this.urdfDisplayMode });
    this.scene.add(this.urdfModel.root);
    this.collisionUrdfModel = await UrdfModel.loadFromText(text, `collision_${name}`, { ...options, displayMode: 'collision' });
    this._tintUrdfGhost(this.collisionUrdfModel, COLLISION_GHOST_COLOR, this._collisionGhostBaseOpacity);
    this.collisionUrdfModel.root.visible = false;
    this.scene.add(this.collisionUrdfModel.root);
    if (this.robotMarker) this.robotMarker.visible = false;
    if (this.robot) this.setRobotPose(this.robot);
    this.setRobotVisible(this.robotVisible);
    this._applyAttachedObjects();
    return this.urdfModel;
  }

  async setRobotDisplayMode(mode) {
    const next = mode === 'visual' ? 'visual' : 'collision';
    if (!this.urdfSourceText || next === this.urdfDisplayMode) return;
    this.urdfDisplayMode = next;
    if (this.urdfModel) this.scene.remove(this.urdfModel.root);
    this.urdfModel = await UrdfModel.loadFromText(this.urdfSourceText, this.urdfSourceName || 'robot.urdf', { ...(this.urdfLoadOptions || {}), displayMode: next });
    this.scene.add(this.urdfModel.root);
    if (this.robot) this.setRobotPose(this.robot);
  }

  async loadUrdfUrl(url, options = {}) {
    const res = await fetch(url);
    if (!res.ok) throw new Error(`load urdf failed: ${res.status}`);
    return this.loadUrdfText(await res.text(), url.split('/').pop(), options);
  }

  _frameBounds() {
    const cx = (this.bounds.minX + this.bounds.maxX) / 2;
    const cy = (this.bounds.minY + this.bounds.maxY) / 2;
    const span = Math.max(this.bounds.maxX - this.bounds.minX, this.bounds.maxY - this.bounds.minY, 4);
    this.controls.target.set(cx, cy, 0);
    this.camera.position.set(cx, cy - span * 0.85, span * 0.9);
    this.controls.update();
  }

  /** Explicit fit — also re-enables auto-frame until the user pans again. */
  fitToBounds() {
    this.autoFrame = true;
    if (this.viewMode === 'top') this.setTopView();
    else this._frameBounds();
  }

  /** Oblique 3D framing (previous default camera). */
  set3dView() {
    this.viewMode = '3d';
    this.autoFrame = false;
    this.followRobot = false;
    this.camera = this.perspectiveCamera;
    this.controls.object = this.camera;
    this.camera.up.set(0, 0, 1);
    this._frameBounds();
    this.fit();
  }

  /** Reset to an orthographic X-Y top view; pointer input may rotate it only in-plane. */
  setTopView() {
    this.viewMode = 'top';
    let cx = (this.bounds.minX + this.bounds.maxX) / 2;
    let cy = (this.bounds.minY + this.bounds.maxY) / 2;
    let span = Math.max(
      this.bounds.maxX - this.bounds.minX,
      this.bounds.maxY - this.bounds.minY,
      4,
    );
    if (this.robot && Number.isFinite(this.robot.x) && Number.isFinite(this.robot.y)) {
      const pad = Math.max(span * 0.35, 4);
      cx = this.robot.x;
      cy = this.robot.y;
      span = Math.max(span, pad * 2);
    }
    this.autoFrame = false;
    this.followRobot = false;
    this._topHalfHeight = Math.max(span * 0.575, 2.3);
    this.camera = this.topCamera;
    this.controls.object = this.camera;
    this.camera.zoom = 1;
    this.camera.up.set(0, 1, 0);
    this.controls.target.set(cx, cy, 0);
    this.camera.position.set(cx, cy, Math.max(span, 10));
    this.camera.lookAt(cx, cy, 0);
    this.fit();
    this.controls.update();
  }

  _remove(obj) {
    if (!obj) return;
    this.scene.remove(obj);
    obj.geometry?.dispose?.();
    if (obj.material) {
      if (Array.isArray(obj.material)) obj.material.forEach((m) => m.dispose?.());
      else {
        obj.material.map?.dispose?.();
        obj.material.dispose?.();
      }
    }
  }

  _eventPos(e) {
    const r = this.canvas.getBoundingClientRect();
    return { x: e.clientX - r.left, y: e.clientY - r.top, w: r.width, h: r.height };
  }

  _hitWorld(e) {
    const p = this._eventPos(e);
    this._ndc.x = (p.x / p.w) * 2 - 1;
    this._ndc.y = -(p.y / p.h) * 2 + 1;
    this._raycaster.setFromCamera(this._ndc, this.camera);
    const hit = new THREE.Vector3();
    if (this._raycaster.ray.intersectPlane(this._plane, hit)) return hit;
    return null;
  }

  _bindInput() {
    // Allow Escape while dragging (canvas must be focusable).
    if (!this.canvas.hasAttribute('tabindex')) this.canvas.setAttribute('tabindex', '0');
    this.canvas.style.outline = 'none';

    this.canvas.addEventListener('contextmenu', (e) => e.preventDefault());
    this.canvas.addEventListener('pointerdown', (e) => {
      if (!this.onMotionObstacleEdit || !this.motionObstacleEditingEnabled || this.mode !== 'pan' || e.button !== 0) return;
      const p = this._eventPos(e);
      this._ndc.set((p.x / p.w) * 2 - 1, -(p.y / p.h) * 2 + 1);
      this._raycaster.setFromCamera(this._ndc, this.camera);
      const hit = this._raycaster.intersectObjects(this.layer.motionObstacleGizmo.children, true)[0];
      const handle = hit?.object?.userData?.motionGizmo;
      if (!handle || !this._motionDraft) return;
      this._motionObstacleDrag = { obstacle: this._motionDraft, handle, x: e.clientX, y: e.clientY, scale: [...this._motionDraft.scale], pose: [...this._motionDraft.pose], start: this._worldAtZ(e, this._motionDraft.pose[2]) };
      this.controls.enabled = false;
      this.canvas.setPointerCapture?.(e.pointerId);
      e.preventDefault();
      e.stopImmediatePropagation();
    }, true);
    this.canvas.addEventListener('pointermove', (e) => {
      const drag = this._motionObstacleDrag;
      if (!drag) return;
      const axis = new THREE.Vector3(...drag.handle.axis);
      const center = new THREE.Vector3(...drag.obstacle.pose.slice(0, 3));
      const a = center.clone().project(this.camera); const b = center.clone().add(axis).project(this.camera);
      const sx = (b.x - a.x) * this.canvas.clientWidth / 2;
      const sy = -(b.y - a.y) * this.canvas.clientHeight / 2;
      const length = Math.hypot(sx, sy) || 1;
      const perPixel = this.camera.isOrthographicCamera
        ? Math.abs(this.camera.top - this.camera.bottom) / (this.canvas.clientHeight * this.camera.zoom)
        : 2 * Math.tan(THREE.MathUtils.degToRad(this.camera.fov) / 2) * this.camera.position.distanceTo(this.controls.target) / this.canvas.clientHeight;
      const delta = ((e.clientX - drag.x) * sx + (e.clientY - drag.y) * sy) / length * perPixel;
      const next = { ...drag.obstacle, pose: [...drag.obstacle.pose], scale: [...drag.scale] };
      if (drag.handle.kind === 'move-x') next.pose[0] += delta;
      else if (drag.handle.kind === 'move-y') next.pose[1] += delta;
      else if (drag.handle.kind === 'move-z') next.pose[2] += delta;
      else if (drag.handle.kind === 'x') next.scale[0] = Math.max(0.01, drag.scale[0] + delta * 2);
      else if (drag.handle.kind === 'y') next.scale[1] = Math.max(0.01, drag.scale[1] + delta * 2);
      else if (drag.handle.kind === 'z') next.scale[2] = Math.max(0.01, drag.scale[2] + delta * 2);
      else if (drag.handle.kind === 'height') next.scale[1] = Math.max(0.01, drag.scale[1] + delta * 2);
      else next.scale[0] = Math.max(0.01, drag.scale[0] + delta);
      this.onMotionObstacleEdit(next, { kind: drag.handle.kind });
      e.preventDefault();
    }, true);
    this.canvas.addEventListener('pointerup', (e) => {
      if (!this._motionObstacleDrag) return;
      this._motionObstacleDrag = null;
      this.controls.enabled = true;
      this.canvas.releasePointerCapture?.(e.pointerId);
      e.preventDefault();
    }, true);
    this.canvas.addEventListener('pointerdown', (e) => {
      if (this.mode !== 'pan' || this.viewMode !== 'top' || e.button !== 0 || !e.shiftKey || e.ctrlKey || e.metaKey || e.altKey) return;
      this._topRotationDrag = { pointerId: e.pointerId, x: e.clientX };
      this.controls.enabled = false;
      this.canvas.setPointerCapture?.(e.pointerId);
      e.preventDefault();
      e.stopImmediatePropagation();
    }, true);
    this.canvas.addEventListener('pointermove', (e) => {
      const drag = this._topRotationDrag;
      if (!drag || drag.pointerId !== e.pointerId) return;
      const delta = e.clientX - drag.x;
      drag.x = e.clientX;
      this.camera.up.applyAxisAngle(new THREE.Vector3(0, 0, 1), -delta * 0.006).normalize();
      this.camera.lookAt(this.controls.target);
      e.preventDefault();
      e.stopImmediatePropagation();
    }, true);
    const finishTopRotation = (e) => {
      if (!this._topRotationDrag || this._topRotationDrag.pointerId !== e.pointerId) return;
      this._topRotationDrag = null;
      this.canvas.releasePointerCapture?.(e.pointerId);
      this._applyControlsForMode();
      e.preventDefault();
      e.stopImmediatePropagation();
    };
    this.canvas.addEventListener('pointerup', finishTopRotation, true);
    this.canvas.addEventListener('pointercancel', finishTopRotation, true);
    // OrbitControls registers its own pointer handler on the canvas.  Capture
    // the right click first while annotating so camera pan cannot consume it.
    this.canvas.addEventListener('pointerdown', (e) => {
      if (this.mode !== 'motionPointCollection' || e.button !== 2) return;
      const point = this.pickCloudPoint(e);
      if (point) this.onPointPick?.(point);
      else this.onPointPickMiss?.();
      e.preventDefault();
      e.stopImmediatePropagation();
    }, true);
    this.canvas.addEventListener('pointerdown', (e) => {
      if (this.mode === 'pan') return;
      if (this.mode === 'motionPointCollection') return;
      // Left button only for other tools; right/middle leave to OrbitControls.
      if (e.button !== 0) return;
      const hit = this._hitWorld(e);
      if (!hit) return;
      if (this.mode === 'pointPick') {
        const callback = this.onPointPick;
        this.onPointPick = null;
        this.mode = 'pan';
        callback?.({ x: hit.x, y: hit.y, z: hit.z });
        e.preventDefault();
        return;
      }
      if (this.mode === 'goal' || this.mode === 'initialPose') {
        try { this.canvas.focus({ preventScroll: true }); } catch (_) { this.canvas.focus(); }
        this._suppressGoalCommit = false;
        const rawGoal = { x: hit.x, y: hit.y, yaw: this.goal?.yaw || 0 };
        const resolvedGoal = this.resolveGoalPoint(rawGoal, e) || rawGoal;
        if (e.shiftKey && !this.goalShiftSnap) {
          // Shift+拖拽：移动目标位置，保持航向
          this.setGoal(resolvedGoal);
          this._dragGoal = { move: true };
        } else {
          // 按下定点，拖拽调整航向（箭头指向拖拽方向）
          this.setGoal(resolvedGoal);
          this._dragGoal = { aim: true, ox: resolvedGoal.x, oy: resolvedGoal.y };
        }
        e.preventDefault();
        return;
      }
      if (this.mode === 'topo') {
        this.autoFrame = false;
        this.followRobot = false;
        this._raycaster.setFromCamera(this._ndc, this.camera);
        const meshes = this.layer.topo.children.filter((c) => c.isMesh);
        const hits = this._raycaster.intersectObjects(meshes, false);
        if (hits[0]?.object?.userData?.node) {
          const node = hits[0].object.userData.node;
          this.selectedNodeId = node.id;
          this._refreshTopoSelection();
          this.onNodeSelect(node);
        } else {
          this.clearTopoSelection();
          this.onNodeSelect(null);
        }
        e.preventDefault();
        e.stopPropagation();
      }
    });
    window.addEventListener('pointermove', (e) => {
      if (!this._dragGoal) return;
      if (this._dragGoal.move) {
        const hit = this._hitWorld(e);
        if (hit) this.setGoal({ x: hit.x, y: hit.y, yaw: this.goal?.yaw || 0 });
      } else if (this._dragGoal.aim) {
        const hit = this._hitWorld(e);
        if (!hit) return;
        const dx = hit.x - this._dragGoal.ox;
        const dy = hit.y - this._dragGoal.oy;
        if (Math.hypot(dx, dy) > 0.05) {
          this.setGoal({
            x: this._dragGoal.ox,
            y: this._dragGoal.oy,
            yaw: Math.atan2(dy, dx),
          });
        }
      }
    });
    window.addEventListener('pointerup', (e) => {
      if (e.button != null && e.button !== 0) return;
      const dragging = !!this._dragGoal;
      const suppress = this._suppressGoalCommit;
      this._dragGoal = null;
      this._suppressGoalCommit = false;
      if (!dragging || suppress) return;
      const committed = this.goal ? { ...this.goal } : null;
      if (committed && (this.mode === 'goal' || this.mode === 'initialPose')) this.onGoalCommit(committed);
    });
    const onCancelKey = (e) => {
      const isEsc = e.key === 'Escape' || e.key === 'Esc' || e.code === 'Escape';
      if (!isEsc) return;
      if (this.mode === 'motionPointCollection') {
        this.cancelWorldPointCollection();
        e.preventDefault();
        return;
      }
      if (!this._dragGoal || (this.mode !== 'goal' && this.mode !== 'initialPose')) return;
      this._suppressGoalCommit = true;
      this._dragGoal = null;
      this.setGoal(null); // 取消时清除本次目标，不还原旧点
      this.onGoalCancel();
      e.preventDefault();
      e.stopPropagation();
    };
    // capture=true：拖拽中也能收到 ESC（含 iframe 内焦点在 canvas 时）
    window.addEventListener('keydown', onCancelKey, true);
    this.canvas.addEventListener('keydown', onCancelKey, true);
    window.addEventListener('keydown', (e) => {
      if (e.key.toLowerCase() !== 'f' || e.ctrlKey || e.metaKey || e.altKey) return;
      const tag = e.target?.tagName;
      if (tag === 'INPUT' || tag === 'TEXTAREA' || tag === 'SELECT' || e.target?.isContentEditable) return;
      if (this.onFocusRobot()) e.preventDefault();
    }, true);
  }

  pickWorldPoint(callback) {
    this.mode = 'pointPick';
    this.onPointPick = callback;
    this.canvas.style.cursor = 'crosshair';
  }

  collectWorldPoints(callback) {
    this.mode = 'motionPointCollection';
    this.onPointPick = callback;
    this.canvas.style.cursor = 'crosshair';
  }

  cancelWorldPointCollection() {
    if (this.mode !== 'motionPointCollection') return;
    this.mode = 'pan';
    this.onPointPick = null;
    this.canvas.style.cursor = '';
    this.onPointCollectionCancelled();
  }

  focusRobot() {
    if (!this.robot || !Number.isFinite(this.robot.x) || !Number.isFinite(this.robot.y)) return false;
    if (this.viewMode !== '3d') this.setViewMode('3d');
    const { x, y } = this.robot;
    this.autoFrame = false;
    this.followRobot = false;
    // 以世界坐标 X/Y 对角线观察，避免完全侧视时机器人轮廓重叠。
    const offset = new THREE.Vector3(3.8, -3.8, 3.8);
    this.controls.target.set(x, y, 0.7);
    this.camera.position.set(x + offset.x, y + offset.y, 0.7 + offset.z);
    this.controls.update();
    return true;
  }
}

window.MapView = SceneMapView;
window.UrdfLivePanel = null; // dock removed; URDF lives in MapView

/** PCD loader (ascii / binary / binary_compressed) → [{x,y,z}, ...] */
async function loadPcdFile(file) {
  const { positions, count } = await loadPCD(file);
  const out = new Array(count);
  for (let i = 0; i < count; i++) {
    const o = i * 3;
    out[i] = { x: positions[o], y: positions[o + 1], z: positions[o + 2] };
  }
  return out;
}
window.loadPcdFile = loadPcdFile;

window.dispatchEvent(new Event('scene-map-ready'));
