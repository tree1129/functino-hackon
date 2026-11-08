export function yawToQuaternion(yaw) {
  return { qx: 0, qy: 0, qz: Math.sin(yaw / 2), qw: Math.cos(yaw / 2) };
}

export function quaternionToYaw(pose) {
  if (Number.isFinite(pose.yaw)) return pose.yaw;
  const { qx = 0, qy = 0, qz = 0, qw = 1 } = pose;
  return Math.atan2(2 * (qw * qz + qx * qy), 1 - 2 * (qy * qy + qz * qz));
}

export function parsePose(text, frameId) {
  const values = text.trim().split(/[\s,]+/).filter(Boolean).map(Number);
  if (values.some((value) => !Number.isFinite(value))) throw new Error('位姿包含非数字');
  if (values.length === 3) {
    const [x, y, yaw] = values;
    return { x, y, z: 0, ...yawToQuaternion(yaw), frame_id: frameId || 'map' };
  }
  if (values.length === 7) {
    const [x, y, z, qx, qy, qz, qw] = values;
    return { x, y, z, qx, qy, qz, qw, frame_id: frameId || 'map' };
  }
  throw new Error('位姿必须是 x,y,yaw 或 x,y,z,qx,qy,qz,qw');
}

export function sceneGoalToPose(value, frameId) {
  return { x: value.x, y: value.y, z: value.z || 0, ...yawToQuaternion(value.yaw || 0), frame_id: frameId || 'map' };
}
