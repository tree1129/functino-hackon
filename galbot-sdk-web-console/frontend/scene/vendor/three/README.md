# Pinned Three.js runtime

This is the smallest locally hosted subset needed by the shared Robot, Motion,
and Navigation scene. Keeping it local makes the console usable on a robot LAN
without access to a public CDN.

The retained Three.js r170 files are:

- `three.module.js` — renderer and scene primitives.
- `controls/OrbitControls.js` — camera interaction.
- `lines/Line2.js`, `LineGeometry.js`, `LineMaterial.js`,
  `LineSegments2.js`, `LineSegmentsGeometry.js` — pixel-width navigation paths.
- `loaders/GLTFLoader.js` and `utils/BufferGeometryUtils.js` — G1 visual mesh loading.

When introducing another Three.js addon, copy that addon and its static local
dependencies here, then add a smoke test for the page that imports it.
