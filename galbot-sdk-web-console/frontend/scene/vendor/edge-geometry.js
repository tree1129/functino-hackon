import { sampleLine, sampleArc, sampleBSpline, polylineLength, resamplePolylineByChordLength } from './curve-math.js';

export const CURVE_TYPES = ['line', 'arc', 'bspline'];
export const DEFAULT_SAMPLE_CHORD_LENGTH = 0.2;
export const DEFAULT_SAMPLE_COUNT = DEFAULT_SAMPLE_CHORD_LENGTH;
const MIN_SAMPLE_CHORD_LENGTH = 0.01;
const MAX_SAMPLE_CHORD_LENGTH = 10;
const DENSE_SAMPLE_MULTIPLIER = 8;
const MIN_DENSE_SAMPLE_COUNT = 128;

function normalizeSampleChordLength(value) {
  const chordLength = Number(value);
  if (!Number.isFinite(chordLength) || chordLength < MIN_SAMPLE_CHORD_LENGTH || chordLength > MAX_SAMPLE_CHORD_LENGTH) {
    return DEFAULT_SAMPLE_CHORD_LENGTH;
  }
  return chordLength;
}

export function ensureEdgeGeometry(edge, fromNode, toNode) {
  if (!edge.geometry) {
    edge.geometry = createDefaultGeometry(fromNode, toNode);
  }
  if (!edge.geometry.curve_type) edge.geometry.curve_type = 'line';
  edge.geometry.sample_count = normalizeSampleChordLength(edge.geometry.sample_count);
  if (!edge.geometry.params) edge.geometry.params = {};
  syncGeometryEndpoints(edge, fromNode, toNode);
  return edge.geometry;
}

export function createDefaultGeometry(fromNode, toNode) {
  const mid = midpoint(fromNode.pose, toNode.pose);
  return {
    curve_type: 'line',
    sample_count: DEFAULT_SAMPLE_COUNT,
    params: {
      via: { x: mid.x, y: mid.y },
      control_points: defaultBsplineInterior(fromNode, toNode),
    },
  };
}

function midpoint(a, b) {
  return { x: (a.x + b.x) / 2, y: (a.y + b.y) / 2 };
}

function defaultBsplineInterior(fromNode, toNode) {
  const a = fromNode.pose;
  const b = toNode.pose;
  const cp1 = { x: a.x + (b.x - a.x) * 0.33, y: a.y + (b.y - a.y) * 0.33 };
  const cp2 = { x: a.x + (b.x - a.x) * 0.66, y: a.y + (b.y - a.y) * 0.66 };
  return [cp1, cp2];
}

export function syncGeometryEndpoints(edge, fromNode, toNode) {
  if (!edge.geometry?.params) return;
  const a = fromNode.pose;
  const b = toNode.pose;
  if (edge.geometry.curve_type === 'arc') {
    if (!edge.geometry.params.via) {
      edge.geometry.params.via = midpoint(a, b);
    }
  }
  if (edge.geometry.curve_type === 'bspline') {
    if (!edge.geometry.params.control_points?.length) {
      edge.geometry.params.control_points = defaultBsplineInterior(fromNode, toNode);
    }
  }
}

export function setCurveType(edge, type, fromNode, toNode) {
  ensureEdgeGeometry(edge, fromNode, toNode);
  edge.geometry.curve_type = type;
  syncGeometryEndpoints(edge, fromNode, toNode);
  updateEdgeCostFromGeometry(edge, fromNode, toNode);
}

export function getBsplineControlPoints(edge, fromNode, toNode) {
  ensureEdgeGeometry(edge, fromNode, toNode);
  const interior = edge.geometry.params.control_points || [];
  return [
    { x: fromNode.pose.x, y: fromNode.pose.y },
    ...interior.map((p) => ({ x: p.x, y: p.y })),
    { x: toNode.pose.x, y: toNode.pose.y },
  ];
}

export function sampleEdgePath(edge, fromNode, toNode) {
  const geom = ensureEdgeGeometry(edge, fromNode, toNode);
  const a = { x: fromNode.pose.x, y: fromNode.pose.y };
  const b = { x: toNode.pose.x, y: toNode.pose.y };
  const chordLength = normalizeSampleChordLength(geom.sample_count);
  const roughLength = Math.max(polylineLength([a, b]), chordLength);
  const denseN = Math.max(MIN_DENSE_SAMPLE_COUNT, Math.ceil(roughLength / chordLength) * DENSE_SAMPLE_MULTIPLIER);
  let raw;

  if (geom.curve_type === 'arc') {
    const via = geom.params.via || midpoint(a, b);
    raw = sampleArc(a, via, b, denseN);
  } else if (geom.curve_type === 'bspline') {
    const cps = getBsplineControlPoints(edge, fromNode, toNode);
    raw = sampleBSpline(cps, 3, denseN);
  } else {
    raw = sampleLine(a, b, denseN);
  }
  return resamplePolylineByChordLength(raw, chordLength);
}

export function updateEdgeCostFromGeometry(edge, fromNode, toNode) {
  const pts = sampleEdgePath(edge, fromNode, toNode);
  edge.cost = polylineLength(pts);
}

/** Editable handles for the active edge (not including fixed endpoints). */
export function getEditableHandles(edge, fromNode, toNode) {
  ensureEdgeGeometry(edge, fromNode, toNode);
  const handles = [];
  if (edge.geometry.curve_type === 'arc') {
    const via = edge.geometry.params.via || midpoint(fromNode.pose, toNode.pose);
    handles.push({ kind: 'arc_via', index: 0, x: via.x, y: via.y });
  } else if (edge.geometry.curve_type === 'bspline') {
    const cps = edge.geometry.params.control_points || [];
    cps.forEach((p, i) => {
      handles.push({ kind: 'bspline_cp', index: i, x: p.x, y: p.y });
    });
  }
  return handles;
}

export function moveHandle(edge, handle, x, y) {
  if (!edge.geometry?.params) return;
  if (handle.kind === 'arc_via') {
    edge.geometry.params.via = { x, y };
  } else if (handle.kind === 'bspline_cp') {
    const cps = edge.geometry.params.control_points;
    if (cps[handle.index]) {
      cps[handle.index].x = x;
      cps[handle.index].y = y;
    }
  }
}

export function addBsplineControlPoint(edge, fromNode, toNode, x, y, insertIndex = null) {
  ensureEdgeGeometry(edge, fromNode, toNode);
  if (edge.geometry.curve_type !== 'bspline') return -1;
  if (!edge.geometry.params.control_points) {
    edge.geometry.params.control_points = [];
  }
  const cps = edge.geometry.params.control_points;
  if (Number.isInteger(insertIndex)) {
    const idx = Math.max(0, Math.min(insertIndex, cps.length));
    cps.splice(idx, 0, { x, y });
    return idx;
  }
  cps.push({ x, y });
  return cps.length - 1;
}

export function removeBsplineControlPoint(edge, index) {
  const cps = edge.geometry?.params?.control_points;
  if (!cps || index < 0 || index >= cps.length) return false;
  cps.splice(index, 1);
  return true;
}

export function findHandleAt(handles, worldX, worldY, threshold = 0.35) {
  let best = null;
  let bestDist = threshold;
  for (const h of handles) {
    const d = Math.hypot(h.x - worldX, h.y - worldY);
    if (d < bestDist) {
      bestDist = d;
      best = h;
    }
  }
  return best;
}

export function normalizeImportedGeometry(e) {
  const g = e.geometry;
  if (!g) return null;
  let curveType = g.curve_type || 'line';
  if (!CURVE_TYPES.includes(curveType)) curveType = 'line';
  const params = {
    via: g.params?.via ? { x: g.params.via.x, y: g.params.via.y } : null,
    control_points: (g.params?.control_points || []).map((p) => ({ x: p.x, y: p.y })),
  };
  return {
    curve_type: curveType,
    sample_count: normalizeSampleChordLength(g.sample_count),
    params,
  };
}

export function serializeEdgeGeometry(edge, fromNode, toNode) {
  if (!edge.geometry) return null;
  const points = sampleEdgePath(edge, fromNode, toNode).map((p) => ({
    x: Math.round(p.x * 10000) / 10000,
    y: Math.round(p.y * 10000) / 10000,
  }));
  const out = {
    type: 'polyline',
    curve_type: edge.geometry.curve_type || 'line',
    sample_count: normalizeSampleChordLength(edge.geometry.sample_count),
    points,
  };
  const params = {};
  if (edge.geometry.curve_type === 'arc' && edge.geometry.params?.via) {
    params.via = {
      x: Math.round(edge.geometry.params.via.x * 10000) / 10000,
      y: Math.round(edge.geometry.params.via.y * 10000) / 10000,
    };
  }
  if (edge.geometry.curve_type === 'bspline' && edge.geometry.params?.control_points?.length) {
    params.control_points = edge.geometry.params.control_points.map((p) => ({
      x: Math.round(p.x * 10000) / 10000,
      y: Math.round(p.y * 10000) / 10000,
    }));
  }
  if (Object.keys(params).length) out.params = params;
  return out;
}
