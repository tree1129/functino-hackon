# Robot assets

This directory is the stable asset mount point for the web console. Each model
gets one subdirectory:

```text
assets/robots/g1/
  visual/                 # detailed URDF meshes and textures
  collision/              # simplified collision URDF/geometries
  manifest.json           # URDF paths and package URI mapping
```

`g1/visual/` is a self-contained Apache-2.0 visual-asset snapshot from
`GalaxyGeneralRobotics/galbot_one_golf_description` at the commit recorded in
its manifest. It contains the G1 URDF plus the GLB visual meshes required by
that URDF. The project root is not a Git repository, so Git cannot record a
submodule here; the manifest is the explicit source boundary until this
workspace is initialized as a repository and the snapshot is replaced by a
real submodule.

The collision URDF currently available from the old visualizer is copied into
`assets/robots/g1/collision/galbot_g1_sphere.urdf`. It is the simplified model
used for collision-oriented display and is separate from the detailed visual
asset.
