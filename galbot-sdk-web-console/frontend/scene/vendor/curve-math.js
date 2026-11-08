/** @typedef {{ x: number, y: number }} Point2 */

export function sampleLine(a, b, segments = 16) {
  const pts = [];
  for (let i = 0; i <= segments; i++) {
    const t = i / segments;
    pts.push({ x: a.x + (b.x - a.x) * t, y: a.y + (b.y - a.y) * t });
  }
  return pts;
}

export function polylineLength(points) {
  let len = 0;
  for (let i = 1; i < points.length; i++) {
    len += Math.hypot(points[i].x - points[i - 1].x, points[i].y - points[i - 1].y);
  }
  return len;
}

export function resamplePolylineByChordLength(points, chordLength = 0.2) {
  if (!points.length) return [];
  const desiredChord = Number.isFinite(chordLength) && chordLength > 1e-9 ? chordLength : 0.2;

  const cumulative = [0];
  for (let i = 1; i < points.length; i++) {
    const d = Math.hypot(points[i].x - points[i - 1].x, points[i].y - points[i - 1].y);
    cumulative.push(cumulative[i - 1] + d);
  }

  const total = cumulative[cumulative.length - 1];
  const segmentCount = Math.max(1, Math.ceil(total / desiredChord));
  if (points.length === 1 || total <= 1e-9) {
    const p = points[0];
    return Array.from({ length: segmentCount + 1 }, () => ({ x: p.x, y: p.y }));
  }

  const out = [];
  let span = 1;
  for (let i = 0; i <= segmentCount; i++) {
    const target = (total * i) / segmentCount;
    while (span < cumulative.length - 1 && cumulative[span] < target) {
      span++;
    }

    const prevLen = cumulative[span - 1];
    const nextLen = cumulative[span];
    const denom = nextLen - prevLen;
    const t = denom <= 1e-9 ? 0 : (target - prevLen) / denom;
    const a = points[span - 1];
    const b = points[span];
    out.push({
      x: a.x + (b.x - a.x) * t,
      y: a.y + (b.y - a.y) * t,
    });
  }
  return out;
}

function circleFromThreePoints(a, b, c) {
  const d = 2 * (a.x * (b.y - c.y) + b.x * (c.y - a.y) + c.x * (a.y - b.y));
  if (Math.abs(d) < 1e-9) return null;
  const a2 = a.x * a.x + a.y * a.y;
  const b2 = b.x * b.x + b.y * b.y;
  const c2 = c.x * c.x + c.y * c.y;
  const ux = (a2 * (b.y - c.y) + b2 * (c.y - a.y) + c2 * (a.y - b.y)) / d;
  const uy = (a2 * (c.x - b.x) + b2 * (a.x - c.x) + c2 * (b.x - a.x)) / d;
  const r = Math.hypot(a.x - ux, a.y - uy);
  if (r < 1e-9) return null;
  return { cx: ux, cy: uy, r };
}

function normAngle(a) {
  while (a <= -Math.PI) a += Math.PI * 2;
  while (a > Math.PI) a -= Math.PI * 2;
  return a;
}

function arcContainsVia(aAng, bAng, viaAng, ccw) {
  const span = ccw ? normAngle(bAng - aAng) : normAngle(aAng - bAng);
  const viaSpan = ccw ? normAngle(viaAng - aAng) : normAngle(aAng - viaAng);
  return viaSpan > 0 && viaSpan < span;
}

/** Circular arc through start, via, end. */
export function sampleArc(a, via, b, segments = 32) {
  const circle = circleFromThreePoints(a, via, b);
  if (!circle) return sampleLine(a, b, segments);

  const aAng = Math.atan2(a.y - circle.cy, a.x - circle.cx);
  const bAng = Math.atan2(b.y - circle.cy, b.x - circle.cx);
  const viaAng = Math.atan2(via.y - circle.cy, via.x - circle.cx);

  const ccw = arcContainsVia(aAng, bAng, viaAng, true);
  let start = aAng;
  let end = bAng;
  let sweep;
  if (ccw) {
    sweep = normAngle(end - start);
    if (sweep <= 0) sweep += Math.PI * 2;
  } else {
    sweep = normAngle(start - end);
    if (sweep <= 0) sweep += Math.PI * 2;
  }

  const pts = [];
  for (let i = 0; i <= segments; i++) {
    const t = i / segments;
    const ang = ccw ? start + sweep * t : start - sweep * t;
    pts.push({
      x: circle.cx + circle.r * Math.cos(ang),
      y: circle.cy + circle.r * Math.sin(ang),
    });
  }
  return pts;
}

function buildClampedKnots(n, degree) {
  const knots = [];
  const m = n + degree + 1;
  for (let i = 0; i <= m; i++) {
    if (i <= degree) knots.push(0);
    else if (i >= n + 1) knots.push(1);
    else knots.push((i - degree) / (n - degree + 1));
  }
  return knots;
}

function deBoor(t, degree, knots, points) {
  const n = points.length - 1;
  const k = findKnotSpan(t, degree, knots, n);
  const d = [];
  for (let j = 0; j <= degree; j++) {
    d[j] = { x: points[k - degree + j].x, y: points[k - degree + j].y };
  }
  for (let r = 1; r <= degree; r++) {
    for (let j = degree; j >= r; j--) {
      const i = k - degree + j;
      const denom = knots[i + degree - r + 1] - knots[i];
      const alpha = denom === 0 ? 0 : (t - knots[i]) / denom;
      d[j] = {
        x: (1 - alpha) * d[j - 1].x + alpha * d[j].x,
        y: (1 - alpha) * d[j - 1].y + alpha * d[j].y,
      };
    }
  }
  return d[degree];
}

function findKnotSpan(t, degree, knots, n) {
  if (t >= knots[n + 1]) return n;
  if (t <= knots[degree]) return degree;
  let low = degree;
  let high = n + 1;
  let mid = Math.floor((low + high) / 2);
  while (t < knots[mid] || t >= knots[mid + 1]) {
    if (t < knots[mid]) high = mid;
    else low = mid;
    mid = Math.floor((low + high) / 2);
  }
  return mid;
}

/** Open uniform clamped B-spline (degree 3 by default). */
export function sampleBSpline(controlPoints, degree = 3, segments = 48) {
  const n = controlPoints.length - 1;
  if (n < 1) return controlPoints.map((p) => ({ x: p.x, y: p.y }));
  const p = Math.min(degree, n);
  if (n < p) return sampleLine(controlPoints[0], controlPoints[n], segments);

  const knots = buildClampedKnots(n, p);
  const t0 = knots[p];
  const t1 = knots[n + 1];
  const pts = [];
  for (let i = 0; i <= segments; i++) {
    const t = t0 + ((t1 - t0) * i) / segments;
    pts.push(deBoor(t, p, knots, controlPoints));
  }
  return pts;
}
