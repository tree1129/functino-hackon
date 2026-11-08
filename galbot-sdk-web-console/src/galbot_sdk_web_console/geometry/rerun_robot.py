"""Rerun-specific loading of the repository's robot model snapshots."""

from __future__ import annotations

import json
import tempfile
import uuid
import xml.etree.ElementTree as ET
from pathlib import Path
from typing import Any


REPOSITORY_ROOT = Path(__file__).resolve().parents[3]
ROBOT_ASSETS_ROOT = REPOSITORY_ROOT / "assets" / "robots"
RERUN_APPLICATION_ID = "galbot_data_playback"
RECORDING_FORMAT_NAME = "galbot.rerun.recording"
RECORDING_FORMAT_VERSION = 1


def robot_model_recording_id(model: str) -> uuid.UUID:
    """Return the stable Store ID shared by a model asset and its telemetry."""
    return uuid.uuid5(uuid.NAMESPACE_URL, f"galbot-sdk-web-console/robot-model/{model}")


def robot_model_recording_path(
    model: str,
    assets_root: Path = ROBOT_ASSETS_ROOT,
    representation: str = "collision",
) -> Path:
    filename = "model.rrd" if representation == "collision" else f"model-{representation}.rrd"
    return assets_root / model / "rerun" / filename


def robot_model_descriptor(
    model: str,
    assets_root: Path = ROBOT_ASSETS_ROOT,
    representation: str = "visual",
) -> dict[str, Any] | None:
    """Return stable, repository-relative provenance for an available robot model."""
    manifest_path = assets_root / model / "manifest.json"
    if not manifest_path.is_file():
        return None
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    if representation not in {"collision", "visual"}:
        raise ValueError(f"unknown robot model representation: {representation}")
    urdf_key = "collision_urdf" if representation == "collision" else "visual_urdf"
    urdf = assets_root / model / str(manifest[urdf_key])
    if not urdf.is_file():
        return None
    return {
        "model": model,
        "representation": representation,
        "manifest_path": manifest_path.relative_to(REPOSITORY_ROOT).as_posix(),
        "urdf_path": urdf.relative_to(REPOSITORY_ROOT).as_posix(),
        "visual_urdf_path": (assets_root / model / str(manifest["visual_urdf"])).relative_to(REPOSITORY_ROOT).as_posix(),
        "source": manifest.get("source", {}),
        "display_mode": "strict_urdf_joint_animation",
    }


def load_rerun_urdf_tree(
    model: str,
    assets_root: Path = ROBOT_ASSETS_ROOT,
    representation: str = "visual",
):
    """Load a model snapshot after normalizing URDF details required by Rerun.

    The returned tree contains model geometry and the URDF's neutral static
    transforms. Recorded joint scalars are intentionally not applied to it.
    """
    from rerun.urdf import UrdfTree

    descriptor = robot_model_descriptor(model, assets_root, representation)
    if descriptor is None:
        raise FileNotFoundError(f"robot model assets are unavailable: {model}")
    manifest_path = REPOSITORY_ROOT / descriptor["manifest_path"]
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    urdf_path = REPOSITORY_ROOT / descriptor["urdf_path"]
    document = ET.parse(urdf_path)
    root = document.getroot()
    for origin in root.iter("origin"):
        origin.attrib.setdefault("xyz", "0 0 0")
        origin.attrib.setdefault("rpy", "0 0 0")
    for axis in root.iter("axis"):
        axis.attrib.setdefault("xyz", "1 0 0")
    if representation == "visual":
        # The visual snapshot intentionally does not ship every collision STL.
        for link in root.iter("link"):
            for collision in list(link.findall("collision")):
                link.remove(collision)
            # UrdfTree only emits a transform entity for links that carry
            # renderable geometry.  The visual URDF contains a number of
            # structural mount/frame links without either visual or collision
            # elements; omitting them breaks the transform chain in Rerun
            # playback (the meshes below those links then report missing
            # Transform3D components).  Add a microscopic, fully transparent
            # marker so those links remain part of the recorded hierarchy.
            if not link.findall("visual"):
                marker = ET.SubElement(link, "visual", {"name": "__rerun_transform_marker"})
                ET.SubElement(marker, "origin", {"xyz": "0 0 0", "rpy": "0 0 0"})
                geometry = ET.SubElement(marker, "geometry")
                ET.SubElement(geometry, "box", {"size": "0.001 0.001 0.001"})
                material = ET.SubElement(marker, "material", {"name": "__rerun_transform_marker_material"})
                ET.SubElement(material, "color", {"rgba": "0 0 0 0"})
    else:
        # The collision URDF also contains high-detail visual mesh references.
        # Turn only its primitive collision shapes into visible geometry for a
        # small, fast playback model.
        root.attrib["name"] = f"galbot_{model}_collision"
        collision_color = "0.95 0.48 0.10 1.0"
        for link in root.iter("link"):
            for visual in list(link.findall("visual")):
                link.remove(visual)
            for collision_index, collision in enumerate(list(link.findall("collision"))):
                if collision.find("geometry/mesh") is not None:
                    link.remove(collision)
                else:
                    collision.tag = "visual"
                    link_name = link.attrib.get("name", "")
                    material_name = f"playback_collision_{link_name or 'link'}_{collision_index}"
                    material = ET.SubElement(collision, "material", {"name": material_name})
                    ET.SubElement(material, "color", {"rgba": collision_color})
        # Keep the same model root frame as the detailed G1 snapshot so one
        # telemetry recording can drive either representation.
        if root.find("link[@name='base_footprint']") is None:
            ET.SubElement(root, "link", {"name": "base_footprint"})
            root_joint = ET.SubElement(root, "joint", {"name": "playback_base_footprint_joint", "type": "fixed"})
            ET.SubElement(root_joint, "origin", {"xyz": "0 0 0", "rpy": "0 0 0"})
            ET.SubElement(root_joint, "parent", {"link": "base_footprint"})
            ET.SubElement(root_joint, "child", {"link": "base_link"})
    for mesh in root.iter("mesh"):
        filename = mesh.attrib.get("filename", "")
        if not filename.startswith("package://"):
            continue
        package_path = filename.removeprefix("package://")
        package, separator, relative = package_path.partition("/")
        package_root = manifest.get("package_roots", {}).get(package)
        if not separator or not package_root:
            continue
        mesh.attrib["filename"] = (assets_root / model / package_root / relative).resolve().as_posix()
    temporary_path: Path | None = None
    try:
        with tempfile.NamedTemporaryFile(suffix=".urdf", delete=False) as temporary:
            temporary_path = Path(temporary.name)
            document.write(temporary, encoding="utf-8", xml_declaration=True)
        tree = UrdfTree.from_file_path(
            temporary_path,
            # Rerun treats this argument as one path segment, so it must not
            # contain '/'. The imported robot then lives at /world/galbot_g1.
            entity_path_prefix="world",
            frame_prefix="robot_model/",
            static_transform_entity_path="world/robot/model/transforms",
        )
    finally:
        if temporary_path is not None:
            temporary_path.unlink(missing_ok=True)
    root_frame = f"robot_model/{tree.root_link().name}"
    return tree, {**descriptor, "root_frame": root_frame}


def write_robot_model_recording(
    model: str,
    destination: Path | None = None,
    *,
    representation: str = "collision",
    force: bool = False,
) -> Path:
    """Write the reusable geometry store loaded alongside small telemetry RRDs."""
    import rerun as rr

    path = (destination or robot_model_recording_path(model, representation=representation)).resolve()
    if path.exists() and not force:
        raise FileExistsError(f"refusing to overwrite existing model recording: {path}")
    path.parent.mkdir(parents=True, exist_ok=True)
    tree, descriptor = load_rerun_urdf_tree(model, representation=representation)
    stream = rr.RecordingStream(RERUN_APPLICATION_ID, recording_id=robot_model_recording_id(model))
    stream.save(path)
    tree.log_urdf_to_recording(stream)
    stream.log("/world", rr.ViewCoordinates.RIGHT_HAND_Z_UP, static=True)
    stream.log("/world", rr.CoordinateFrame("map"), static=True)
    stream.log(
        "/world/robot/model/root",
        rr.Transform3D(parent_frame="base_link", child_frame=descriptor["root_frame"]),
        static=True,
    )
    stream.log(
        "/metadata/robot_model_asset",
        rr.TextDocument(json.dumps(descriptor, ensure_ascii=False), media_type="application/json"),
        static=True,
    )
    stream.flush(timeout_sec=30)
    stream.disconnect()
    return path


def recording_blueprint():
    """Default diagnostic layout: one unified world scene plus joint plots."""
    import rerun.blueprint as rrb

    return rrb.Blueprint(
        rrb.Tabs(
            rrb.Spatial3DView(
                name="Robot Scene",
                origin="/world",
                contents="/world/**",
            ),
            rrb.TimeSeriesView(
                name="Joint Positions",
                origin="/robot/joints",
                contents="/robot/joints/**",
            ),
        ),
        rrb.SelectionPanel(state="collapsed"),
        rrb.TimePanel(state="expanded", timeline="capture_time"),
        auto_views=False,
    )
