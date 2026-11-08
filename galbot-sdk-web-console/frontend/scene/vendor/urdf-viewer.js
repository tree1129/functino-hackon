import * as THREE from 'three';
import { GLTFLoader } from 'three/addons/loaders/GLTFLoader.js';
import { approxSimpObjectSpheres } from './simp-object-spheres.js';

const COLLISION_COLOR = 0xff6b35;
const VISUAL_COLOR = 0x4a9eff;
const ATTACH_SPHERE_COLOR = 0x5dff9a;

function parseNumbers(str) {
  if (!str) return [0, 0, 0];
  return str.trim().split(/\s+/).map(Number);
}

function rpyToQuaternion(rpy) {
  // URDF fixed-axis roll-pitch-yaw ≡ Three.js Euler ZYX
  const euler = new THREE.Euler(rpy[0], rpy[1], rpy[2], 'ZYX');
  return new THREE.Quaternion().setFromEuler(euler);
}

function applyOrigin(group, originEl) {
  if (!originEl) return;
  const xyz = parseNumbers(originEl.getAttribute('xyz'));
  const rpy = parseNumbers(originEl.getAttribute('rpy'));
  group.position.set(xyz[0], xyz[1], xyz[2]);
  group.quaternion.copy(rpyToQuaternion(rpy));
}

function createPrimitiveGeometry(geoEl) {
  if (!geoEl) return null;
  const sphere = geoEl.querySelector('sphere');
  if (sphere) {
    const r = parseFloat(sphere.getAttribute('radius') || '0.1');
    return new THREE.SphereGeometry(r, 24, 16);
  }
  const box = geoEl.querySelector('box');
  if (box) {
    const size = parseNumbers(box.getAttribute('size'));
    return new THREE.BoxGeometry(size[0], size[1], size[2]);
  }
  const cylinder = geoEl.querySelector('cylinder');
  if (cylinder) {
    const r = parseFloat(cylinder.getAttribute('radius') || '0.1');
    const l = parseFloat(cylinder.getAttribute('length') || '0.2');
    return new THREE.CylinderGeometry(r, r, l, 24);
  }
  const mesh = geoEl.querySelector('mesh');
  if (mesh) {
    return { type: 'mesh', filename: mesh.getAttribute('filename') || '' };
  }
  return null;
}

async function addGeometryToGroup(parent, originEl, geoEl, color, opacity = 0.55, options = {}) {
  const prim = createPrimitiveGeometry(geoEl);
  if (!prim) return false;
  if (prim.type === 'mesh') {
    if (!options.resolveMeshUrl) return false;
    const url = options.resolveMeshUrl(prim.filename);
    if (!url || !/\.glb(?:$|[?#])/i.test(url)) return false;
    const loaded = await new GLTFLoader().loadAsync(url);
    const group = new THREE.Group();
    applyOrigin(group, originEl);
    const scale = parseNumbers(geoEl.querySelector('mesh')?.getAttribute('scale') || '1 1 1');
    group.scale.set(scale[0], scale[1], scale[2]);
    loaded.scene.traverse((object) => {
      if (!object.isMesh) return;
      object.castShadow = true;
      object.receiveShadow = true;
    });
    group.add(loaded.scene);
    parent.add(group);
    return true;
  }

  const group = new THREE.Group();
  applyOrigin(group, originEl);
  const mat = new THREE.MeshPhongMaterial({
    color,
    transparent: opacity < 1,
    opacity,
    depthWrite: opacity >= 0.9,
  });
  const mesh = new THREE.Mesh(prim, mat);
  group.add(mesh);
  parent.add(group);
  return true;
}

/**
 * Lightweight URDF loader for collision debugging (sphere/box/cylinder).
 */
export class UrdfModel {
  constructor() {
    this.root = new THREE.Group();
    this.root.name = 'urdf_root';
    this.linkGroups = new Map();
    this.joints = [];
    this.jointMap = new Map();
    this.rootLinkName = null;
    this.robotName = '';
    this.missingMeshes = 0;
    this.collisionCount = 0;
    /** @type {Map<string, {name:string, parentLink:string, group:THREE.Group, mesh:THREE.Mesh}>} */
    this.attachedObjects = new Map();
    this._attachSeq = 0;
  }

  static async loadFromText(text, filename = 'robot.urdf', options = {}) {
    const doc = new DOMParser().parseFromString(text, 'text/xml');
    const parserError = doc.querySelector('parsererror');
    if (parserError) throw new Error('Invalid URDF XML');

    const robotEl = doc.querySelector('robot');
    if (!robotEl) throw new Error('URDF missing <robot> element');

    const model = new UrdfModel();
    model.robotName = robotEl.getAttribute('name') || filename;
    model.displayMode = options.displayMode || 'collision';
    await model._parse(robotEl, model.displayMode, options);
    model.resetJoints(0);
    return model;
  }

  static async loadFromFile(file, options = {}) {
    const text = await file.text();
    return UrdfModel.loadFromText(text, file.name, options);
  }

  async _parse(robotEl, displayMode = 'collision', options = {}) {
    const linkEls = [...robotEl.querySelectorAll(':scope > link')];
    const jointEls = [...robotEl.querySelectorAll(':scope > joint')];

    for (const linkEl of linkEls) {
      const name = linkEl.getAttribute('name');
      const group = new THREE.Group();
      group.name = name;
      this.linkGroups.set(name, group);

      let added = false;
      const geometries = displayMode === 'visual' ? [...linkEl.querySelectorAll('visual')] : [...linkEl.querySelectorAll('collision')];
      for (const col of geometries) {
        const geoEl = col.querySelector('geometry');
        if (await addGeometryToGroup(group, col.querySelector('origin'), geoEl, displayMode === 'visual' ? VISUAL_COLOR : COLLISION_COLOR, displayMode === 'visual' ? 1 : 0.6, options)) {
          added = true;
          this.collisionCount += 1;
        } else if (geoEl?.querySelector('mesh')) {
          this.missingMeshes += 1;
        }
      }
      if (!added && displayMode === 'auto') {
        for (const vis of linkEl.querySelectorAll('visual')) {
          const geoEl = vis.querySelector('geometry');
          if (await addGeometryToGroup(group, vis.querySelector('origin'), geoEl, VISUAL_COLOR, 0.75, options)) {
            added = true;
          } else if (geoEl?.querySelector('mesh')) {
            this.missingMeshes += 1;
          }
        }
      }
      // Empty links are still kept in the kinematic tree, but a visible
      // placeholder sphere makes fine visual models look as if they contain
      // extra geometry (especially around gripper flange/TCP links). Keep the
      // marker as an opt-in diagnostic aid only.
      if (!added && options.showLinkMarkers === true) {
        const marker = new THREE.Mesh(
          new THREE.SphereGeometry(0.05, 8, 6),
          new THREE.MeshPhongMaterial({ color: 0x888888, transparent: true, opacity: 0.8 }),
        );
        group.add(marker);
      }
    }

    const childLinks = new Set();
    for (const jointEl of jointEls) {
      const child = jointEl.querySelector('child')?.getAttribute('link');
      if (child) childLinks.add(child);
    }
    for (const [name] of this.linkGroups) {
      if (!childLinks.has(name)) {
        this.rootLinkName = name;
        break;
      }
    }
    if (!this.rootLinkName) {
      this.rootLinkName = [...this.linkGroups.keys()][0];
    }

    const jointRecords = jointEls.map((jointEl) => {
      const name = jointEl.getAttribute('name');
      const type = jointEl.getAttribute('type') || 'fixed';
      const parent = jointEl.querySelector('parent')?.getAttribute('link');
      const child = jointEl.querySelector('child')?.getAttribute('link');
      const originEl = jointEl.querySelector('origin');
      const axisEl = jointEl.querySelector('axis');
      const axis = parseNumbers(axisEl?.getAttribute('xyz') || '0 0 1');
      const axisVec = new THREE.Vector3(axis[0], axis[1], axis[2]).normalize();
      const limitEl = jointEl.querySelector('limit');
      const lower = limitEl ? parseFloat(limitEl.getAttribute('lower') || '-3.14') : -Math.PI;
      const upper = limitEl ? parseFloat(limitEl.getAttribute('upper') || '3.14') : Math.PI;

      const jointGroup = new THREE.Group();
      jointGroup.name = `joint_${name}`;
      applyOrigin(jointGroup, originEl);

      const motionGroup = new THREE.Group();
      motionGroup.name = `motion_${name}`;
      jointGroup.add(motionGroup);

      return {
        name,
        type,
        parent,
        child,
        jointGroup,
        motionGroup,
        axis: axisVec,
        lower,
        upper,
        value: 0,
      };
    });

    for (const j of jointRecords) {
      this.joints.push(j);
      this.jointMap.set(j.name, j);
    }

    // Build kinematic tree: child link -> joint motion frame -> parent link
    for (const joint of jointRecords) {
      const childGroup = this.linkGroups.get(joint.child);
      if (childGroup) joint.motionGroup.add(childGroup);
    }
    for (const joint of jointRecords) {
      const parentGroup = this.linkGroups.get(joint.parent);
      if (parentGroup) parentGroup.add(joint.jointGroup);
    }

    const rootGroup = this.linkGroups.get(this.rootLinkName);
    if (rootGroup) this.root.add(rootGroup);
  }

  getMovableJoints() {
    return this.joints.filter((j) => j.type === 'revolute' || j.type === 'continuous' || j.type === 'prismatic');
  }

  setJointValue(name, value) {
    const joint = this.jointMap.get(name);
    if (!joint) return;
    joint.value = value;
    this._applyJoint(joint);
  }

  _applyJoint(joint) {
    joint.motionGroup.quaternion.set(0, 0, 0, 1);
    joint.motionGroup.position.set(0, 0, 0);

    if (joint.type === 'revolute' || joint.type === 'continuous') {
      joint.motionGroup.quaternion.setFromAxisAngle(joint.axis, joint.value);
    } else if (joint.type === 'prismatic') {
      joint.motionGroup.position.copy(joint.axis).multiplyScalar(joint.value);
    }
  }

  resetJoints(value = 0) {
    for (const joint of this.getMovableJoints()) {
      joint.value = value;
      this._applyJoint(joint);
    }
  }

  setBasePose(x, y, z, roll, pitch, yaw) {
    this.root.position.set(x, y, z);
    const euler = new THREE.Euler(roll, pitch, yaw, 'ZYX');
    this.root.quaternion.setFromEuler(euler);
  }

  setBasePoseQuaternion(x, y, z, qx, qy, qz, qw) {
    this.root.position.set(x, y, z);
    this.root.quaternion.set(qx, qy, qz, qw);
  }

  getBasePose() {
    const e = new THREE.Euler().setFromQuaternion(this.root.quaternion, 'ZYX');
    return {
      x: this.root.position.x,
      y: this.root.position.y,
      z: this.root.position.z,
      roll: e.x,
      pitch: e.y,
      yaw: e.z,
      qx: this.root.quaternion.x,
      qy: this.root.quaternion.y,
      qz: this.root.quaternion.z,
      qw: this.root.quaternion.w,
    };
  }

  getBoundingBox(target = new THREE.Box3()) {
    target.makeEmpty();
    this.root.updateMatrixWorld(true);
    return target.setFromObject(this.root);
  }

  getLinkNames() {
    return [...this.linkGroups.keys()].sort();
  }

  listAttachedObjects() {
    return [...this.attachedObjects.values()].map((o) => ({
      name: o.name,
      parentLink: o.parentLink,
      type: o.type,
      sphereCount: o.sphereCount || 0,
      useSpheres: !!o.useSpheres,
    }));
  }

  /**
   * Attach a simple geometry fixed to a URDF link (same semantics as
   * esdfCollisionInterface::attachObjectFromSimpObj / RobotPage::onAttachObject).
   * By default visualizes collision sphere packing (球体替代).
   * @param {{
   *   name?: string,
   *   parentLink: string,
   *   type: 'BOX'|'SPHERE'|'CYLINDER'|'PLANE',
   *   translation?: [number,number,number],
   *   quaternion?: [number,number,number,number], // qx,qy,qz,qw
   *   length?: number, width?: number, height?: number, radius?: number,
   *   resolution?: number,
   *   useSpheres?: boolean,
   *   allowRename?: boolean,
   *   color?: number,
   *   opacity?: number,
   * }} spec
   */
  attachSimpObject(spec) {
    const parentLink = (spec.parentLink || '').trim();
    if (!parentLink) throw new Error('parent link 为空');
    const linkGroup = this.linkGroups.get(parentLink);
    if (!linkGroup) throw new Error(`找不到 link: ${parentLink}`);

    let name = (spec.name || '').trim();
    if (!name) {
      name = `add_obj_${this._attachSeq++}`;
    }
    if (this.attachedObjects.has(name)) {
      if (!spec.allowRename) {
        throw new Error(`物体已存在: ${name}`);
      }
      while (this.attachedObjects.has(name)) {
        name = `add_obj_${this._attachSeq++}`;
      }
    }

    const type = String(spec.type || 'BOX').toUpperCase();
    const color = spec.color ?? ATTACH_SPHERE_COLOR;
    const opacity = spec.opacity ?? 0.55;
    const useSpheres = spec.useSpheres !== false;
    const mat = new THREE.MeshPhongMaterial({
      color,
      transparent: opacity < 1,
      opacity,
      depthWrite: opacity >= 0.9,
    });

    const group = new THREE.Group();
    group.name = `attach_${name}`;
    const t = spec.translation || [0, 0, 0];
    const q = spec.quaternion || [0, 0, 0, 1];
    group.position.set(t[0] || 0, t[1] || 0, t[2] || 0);
    group.quaternion.set(q[0] || 0, q[1] || 0, q[2] || 0, q[3] ?? 1);

    /** @type {THREE.Object3D[]} */
    const meshes = [];
    let sphereCount = 0;

    if (useSpheres) {
      const { centers, radii } = approxSimpObjectSpheres({
        type,
        length: Number(spec.length ?? 0.1),
        width: Number(spec.width ?? 0.1),
        height: Number(spec.height ?? 0.1),
        radius: Number(spec.radius ?? 0.05),
        resolution: Number(spec.resolution ?? 0.15),
      });
      sphereCount = centers.length;
      // Reuse geometries keyed by rounded radius to cut draw cost.
      const geoCache = new Map();
      for (let i = 0; i < centers.length; i += 1) {
        const r = Math.max(1e-4, radii[i]);
        const key = r.toFixed(4);
        let geo = geoCache.get(key);
        if (!geo) {
          geo = new THREE.SphereGeometry(r, 12, 10);
          geoCache.set(key, geo);
        }
        const mesh = new THREE.Mesh(geo, mat);
        mesh.position.set(centers[i][0], centers[i][1], centers[i][2]);
        group.add(mesh);
        meshes.push(mesh);
      }
    } else {
      let geometry = null;
      if (type === 'BOX') {
        geometry = new THREE.BoxGeometry(
          Number(spec.length ?? 0.1),
          Number(spec.width ?? 0.1),
          Number(spec.height ?? 0.1),
        );
      } else if (type === 'SPHERE') {
        geometry = new THREE.SphereGeometry(Number(spec.radius ?? 0.05), 24, 16);
      } else if (type === 'CYLINDER') {
        const r = Number(spec.radius ?? 0.05);
        const h = Number(spec.height ?? 0.1);
        geometry = new THREE.CylinderGeometry(r, r, h, 24);
      } else if (type === 'PLANE') {
        geometry = new THREE.BoxGeometry(
          Number(spec.length ?? 0.2),
          Number(spec.width ?? 0.2),
          0.002,
        );
      } else {
        throw new Error(`不支持的几何类型: ${type}`);
      }
      const mesh = new THREE.Mesh(geometry, mat);
      if (type === 'CYLINDER') mesh.rotation.x = Math.PI / 2;
      group.add(mesh);
      meshes.push(mesh);
    }

    linkGroup.add(group);

    this.attachedObjects.set(name, {
      name,
      parentLink,
      type,
      group,
      meshes,
      sphereCount,
      useSpheres,
    });
    return name;
  }

  detachObject(name) {
    const rec = this.attachedObjects.get(name);
    if (!rec) return false;
    if (rec.group.parent) rec.group.parent.remove(rec.group);
    const seenGeo = new Set();
    const seenMat = new Set();
    for (const mesh of rec.meshes || (rec.mesh ? [rec.mesh] : [])) {
      if (mesh.geometry && !seenGeo.has(mesh.geometry)) {
        seenGeo.add(mesh.geometry);
        mesh.geometry.dispose();
      }
      if (mesh.material && !seenMat.has(mesh.material)) {
        seenMat.add(mesh.material);
        if (Array.isArray(mesh.material)) mesh.material.forEach((m) => m.dispose());
        else mesh.material.dispose();
      }
    }
    this.attachedObjects.delete(name);
    return true;
  }

  clearAttachedObjects() {
    for (const name of [...this.attachedObjects.keys()]) {
      this.detachObject(name);
    }
  }

  dispose() {
    this.clearAttachedObjects();
    this.root.traverse((obj) => {
      if (obj.geometry) obj.geometry.dispose();
      if (obj.material) {
        if (Array.isArray(obj.material)) obj.material.forEach((m) => m.dispose());
        else obj.material.dispose();
      }
    });
    this.linkGroups.clear();
    this.joints = [];
    this.jointMap.clear();
  }
}
