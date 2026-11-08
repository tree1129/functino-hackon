/**
 * Sphere approximation for simple attach objects.
 * Mirrors aphropm_cplanner esdfCollisionInterface::attachObjectFromSimpObj.
 */

/**
 * @param {{
 *   type: string,
 *   length?: number,
 *   width?: number,
 *   height?: number,
 *   radius?: number,
 *   resolution?: number,
 * }} spec
 * @returns {{ centers: number[][], radii: number[] }}
 */
export function approxSimpObjectSpheres(spec) {
  const type = String(spec.type || 'BOX').toUpperCase();
  if (type === 'SPHERE') {
    const r = Math.max(1e-6, Number(spec.radius ?? 0.05));
    return { centers: [[0, 0, 0]], radii: [r] };
  }
  if (type === 'CYLINDER') {
    return approxCylinder(Number(spec.radius ?? 0.05), Number(spec.height ?? 0.1));
  }
  if (type === 'PLANE') {
    return approxBox(
      Number(spec.length ?? 0.2),
      Number(spec.width ?? 0.2),
      Math.max(1e-4, Number(spec.height ?? 0.002)),
      Number(spec.resolution ?? 0.15),
    );
  }
  // BOX
  return approxBox(
    Number(spec.length ?? 0.1),
    Number(spec.width ?? 0.1),
    Number(spec.height ?? 0.1),
    Number(spec.resolution ?? 0.15),
  );
}

function approxBox(length, width, height, resolution = 0.15) {
  const centers = [];
  const radii = [];
  const dims = [length, width, height].slice().sort((a, b) => a - b);
  const isFlat = dims[0] < 0.25 * dims[1] && dims[0] < 0.25 * dims[2];

  if (isFlat) {
    let flatAxis = 2;
    if (length === dims[0]) flatAxis = 0;
    else if (width === dims[0]) flatAxis = 1;

    const radius = dims[0] / 2;
    let dimA;
    let dimB;
    let axisA;
    let axisB;
    if (flatAxis === 0) {
      dimA = width;
      dimB = height;
      axisA = 1;
      axisB = 2;
    } else if (flatAxis === 1) {
      dimA = length;
      dimB = height;
      axisA = 0;
      axisB = 2;
    } else {
      dimA = length;
      dimB = width;
      axisA = 0;
      axisB = 1;
    }

    const stepsA = Math.max(1, Math.floor(dimA / (2 * radius)));
    const stepsB = Math.max(1, Math.floor(dimB / (2 * radius)));
    const spacingA = dimA / stepsA;
    const spacingB = dimB / stepsB;

    for (let i = 0; i <= stepsA; i += 1) {
      for (let j = 0; j <= stepsB; j += 1) {
        const local = [0, 0, 0];
        local[axisA] = i * spacingA - dimA / 2;
        local[axisB] = j * spacingB - dimB / 2;
        centers.push(local);
        radii.push(radius);
      }
    }
    return { centers, radii };
  }

  const res = resolution > 0 ? resolution : 0.15;
  const desiredSphereDiameter = height < res ? height : res;
  const maxInscribedRadius = 0.5 * Math.min(length, width, height);
  const sphereRadius = Math.min(0.5 * desiredSphereDiameter, maxInscribedRadius);
  if (sphereRadius <= 0) {
    return { centers: [[0, 0, 0]], radii: [0.01] };
  }

  const calcAxisPositions = (dim) => {
    const positions = [0];
    const half = 0.5 * dim;
    const maxAbs = half - sphereRadius;
    const eps = 1e-9;
    if (maxAbs <= eps) return positions;
    for (let layer = 1; ; layer += 1) {
      const offset = layer * desiredSphereDiameter;
      if (offset > maxAbs + eps) break;
      positions.push(offset, -offset);
    }
    let hasPos = false;
    let hasNeg = false;
    for (const p of positions) {
      if (Math.abs(p - maxAbs) <= eps) hasPos = true;
      if (Math.abs(p + maxAbs) <= eps) hasNeg = true;
    }
    if (!hasPos) positions.push(maxAbs);
    if (!hasNeg) positions.push(-maxAbs);
    return positions;
  };

  const xs = calcAxisPositions(length);
  const ys = calcAxisPositions(width);
  const zs = calcAxisPositions(height);
  for (const z of zs) {
    for (const y of ys) {
      for (const x of xs) {
        centers.push([x, y, z]);
        radii.push(sphereRadius);
      }
    }
  }
  return { centers, radii };
}

function approxCylinder(radius, height) {
  const centers = [];
  const radii = [];
  const r = Math.max(1e-6, radius);
  const h = Math.max(1e-6, height);

  if (h <= 2 * r) {
    centers.push([0, 0, 0]);
    radii.push(Math.sqrt(r * r + (h / 2) * (h / 2)));
    return { centers, radii };
  }

  if (h < 0.5 * r) {
    const discRadius = r * 0.75;
    centers.push([0, 0, 0]);
    radii.push(discRadius);
    if (r > 2 * h) {
      const numOuter = Math.max(6, Math.floor((2 * Math.PI * r) / h));
      for (let i = 0; i < numOuter; i += 1) {
        const angle = (2 * Math.PI * i) / numOuter;
        const x = (r - h / 2) * Math.cos(angle);
        const y = (r - h / 2) * Math.sin(angle);
        centers.push([x, y, 0]);
        radii.push(h / 2);
      }
    }
    return { centers, radii };
  }

  const stepSize = 1.5 * r;
  const numSpheres = Math.max(2, Math.floor(h / stepSize));
  for (let i = 0; i < numSpheres; i += 1) {
    const z = (i * h) / (numSpheres - 1) - h / 2;
    centers.push([0, 0, z]);
    radii.push(r);
    if (h > 4 * r && numSpheres > 3 && i > 0 && i < numSpheres - 1) {
      for (let j = 0; j < 4; j += 1) {
        const angle = (2 * Math.PI * j) / 4;
        const radPos = 0.7 * r;
        centers.push([radPos * Math.cos(angle), radPos * Math.sin(angle), z]);
        radii.push(0.7 * r);
      }
    }
  }
  return { centers, radii };
}
