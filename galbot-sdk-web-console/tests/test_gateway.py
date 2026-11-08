import pytest
from fastapi.testclient import TestClient
from fastapi import HTTPException

from galbot_sdk_web_console.api.schemas import MotionExecuteRequest, NavigationGoalRequest, PoseRequest
from galbot_sdk_web_console.gateway import app as gateway_app
from galbot_sdk_web_console.gateway.app import command, get_state, global_cloud, index, navigate, navigation_tasks, relocalize, stop
from galbot_sdk_web_console.gateway.auth import SESSION_COOKIE


def test_pose_request_converts_to_sdk_arraylike() -> None:
    request = PoseRequest(x=1, y=2, z=3, qx=0.1, qy=0.2, qz=0.3, qw=0.9)
    assert request.to_sdk_array() == [1, 2, 3, 0.1, 0.2, 0.3, 0.9]


def test_state_and_navigation_dry_run() -> None:
    assert get_state()["status"] == "IDLE"
    response = navigate(NavigationGoalRequest(x=1, y=2, qz=0.2474, qw=0.9689))
    assert response["accepted"] is True
    assert response["dry_run"] is True
    assert response["target"]["frame_id"] == "map"
    assert navigation_tasks()["tasks"][0]["task_id"] == response["task_id"]


def test_relocalize_and_stop_dry_run() -> None:
    pose = relocalize(PoseRequest(x=0, y=0))
    assert pose["dry_run"] is True
    response = stop()
    assert response["accepted"] is True
    assert response["dry_run"] is True


def test_unknown_generic_command_is_404() -> None:
    with pytest.raises(HTTPException) as error:
        command("unknown", "method", {})
    assert error.value.status_code == 404


def test_generic_command_validates_and_dispatches() -> None:
    response = command("navigation", "navigate_to_goal", {"x": 3, "y": 4})
    assert response["accepted"] is True
    assert response["target"]["x"] == 3

    with pytest.raises(HTTPException) as error:
        command("navigation", "go", {"x": "invalid", "y": 4})
    assert error.value.status_code == 422


def test_root_opens_shared_robot_console() -> None:
    assert index().path.name == "console.html"
    assert index().path.parent.name == "frontend"


def test_legacy_scene_pages_redirect_to_shared_workspaces() -> None:
    with TestClient(gateway_app.app) as client:
        for page, workspace in (("robot.html", "robot"), ("motion.html", "motion"), ("navigation.html", "navigation")):
            response = client.get(f"/frontend/{page}")
            assert response.status_code == 200
            assert f"location.replace('/frontend/console.html#{workspace}')" in response.text
            assert "standalone" not in response.text


def test_scene_bootstrap_is_public_and_independent_of_robot_connection() -> None:
    with TestClient(gateway_app.app) as client:
        response = client.get("/api/scene/bootstrap")
        assert response.status_code == 200
        assert response.json()["model"] == gateway_app.runtime.session.model


def test_frontend_resources_disable_browser_cache() -> None:
    with TestClient(gateway_app.app) as client:
        assert client.get("/").headers["cache-control"] == "no-store"
        assert client.get("/frontend/pages/console.js").headers["cache-control"] == "no-store"
        assert client.get("/frontend/recording.html").headers["cache-control"] == "no-store"


def test_recording_requires_auth_and_explicit_enable(tmp_path, monkeypatch) -> None:
    recorder = gateway_app.runtime.recording
    recorder.cancel()
    monkeypatch.setattr(recorder, "root", tmp_path)
    with TestClient(gateway_app.app) as client:
        assert client.get("/api/recording/status").status_code == 401
        client.post("/api/auth/login", json={"code": gateway_app.auth.access_code})
        capabilities = client.get("/api/recording/capabilities")
        assert capabilities.status_code == 200
        assert "base_pose" in {item["id"] for item in capabilities.json()["sources"]}
        armed = client.post("/api/recording/arm", json={"sources": ["joints", "base_pose"]})
        assert armed.status_code == 200
        assert armed.json()["state"] == "ARMED"
        assert client.post("/api/recording/cancel", json={"confirmed": False}).status_code == 422
        assert client.post("/api/recording/cancel", json={"confirmed": True}).json()["state"] == "INACTIVE"


def test_signed_recording_file_is_available_to_cross_origin_rerun_viewer(tmp_path, monkeypatch) -> None:
    recorder = gateway_app.runtime.recording
    recorder.cancel()
    monkeypatch.setattr(recorder, "root", tmp_path)
    path = tmp_path / "sample.rrd"
    path.write_bytes(b"rrd")
    token = recorder.playback_token("sample")
    with TestClient(gateway_app.app) as client:
        response = client.get(f"/api/recordings/sample/file?token={token}")
        assert response.status_code == 200
        assert response.content == b"rrd"
        assert response.headers["access-control-allow-origin"] == "*"
        assert client.get("/api/recordings/sample/file?token=wrong").status_code == 401
        viewer_response = client.get(f"/api/recording-files/sample.rrd?token={token}")
        assert viewer_response.status_code == 200
        assert viewer_response.content == b"rrd"
        assert "content-disposition" not in viewer_response.headers
        assert viewer_response.headers["access-control-allow-origin"] == "*"
        assert client.get("/api/recording-files/sample.rrd?token=wrong").status_code == 401


def test_authenticated_rrd_import_can_be_played(tmp_path, monkeypatch) -> None:
    recorder = gateway_app.runtime.recording
    recorder.cancel()
    monkeypatch.setattr(recorder, "root", tmp_path)
    with TestClient(gateway_app.app) as client:
        client.post("/api/auth/login", json={"code": gateway_app.auth.access_code})
        response = client.post("/api/recordings/import?filename=local.rrd", content=b"rrd-data", headers={"content-type": "application/octet-stream"})
        assert response.status_code == 200
        item = response.json()
        assert item["imported"] is True
        assert client.get(f"/api/recordings/local/file?token={item['playback_token']}").content == b"rrd-data"


def test_api_requires_login_and_session_is_in_memory() -> None:
    with TestClient(gateway_app.app) as client:
        assert client.get("/api/robot/state").status_code == 401
        assert client.get("/api/auth/status").json() == {"authenticated": False}
        assert client.post("/api/auth/login", json={"code": "wrong"}).status_code == 401
        login = client.post("/api/auth/login", json={"code": gateway_app.auth.access_code})
        assert login.status_code == 200
        assert client.cookies.get(SESSION_COOKIE)
        assert client.get("/api/robot/state").status_code == 200
        assert client.post("/api/auth/logout").json() == {"authenticated": False}
        assert client.get("/api/robot/state").status_code == 401


def test_access_code_is_short_and_normalizes_human_input() -> None:
    code = gateway_app.auth.access_code
    assert len(code) == 8
    assert gateway_app.auth.verify_code(f" {code[:4]}-{code[4:]} ")


def test_each_client_needs_its_own_session() -> None:
    first = TestClient(gateway_app.app)
    second = TestClient(gateway_app.app)
    try:
        assert first.post("/api/auth/login", json={"code": gateway_app.auth.access_code}).status_code == 200
        assert first.get("/api/robot/state").status_code == 200
        assert second.get("/api/robot/state").status_code == 401
        assert second.post("/api/auth/login", json={"code": gateway_app.auth.access_code}).status_code == 200
        assert second.get("/api/robot/state").status_code == 200
        assert first.cookies.get(SESSION_COOKIE) != second.cookies.get(SESSION_COOKIE)
    finally:
        first.close()
        second.close()


def test_static_pages_remain_public_but_control_api_does_not() -> None:
    with TestClient(gateway_app.app) as client:
        assert client.get("/").status_code == 200
        assert client.get("/frontend/pages/console.js").status_code == 200
        assert client.get("/assets/robots/g1/manifest.json").status_code == 200
        assert client.get("/api/scene/bootstrap").status_code == 200
        assert client.get("/api/capabilities").status_code == 401


def test_shared_model_recording_is_public_for_cross_origin_viewer(tmp_path, monkeypatch) -> None:
    model_asset = tmp_path / "model.rrd"
    model_asset.write_bytes(b"shared-model")
    monkeypatch.setattr(gateway_app, "robot_model_recording_path", lambda model: model_asset)
    with TestClient(gateway_app.app) as client:
        response = client.get(f"/api/recording-assets/{gateway_app.runtime.session.model}.rrd")
        assert response.status_code == 200
        assert response.content == b"shared-model"
        assert response.headers["access-control-allow-origin"] == "*"
        assert client.get("/api/recording-assets/unknown.rrd").status_code == 404


def test_signed_playback_endpoint_serves_assembled_recording(tmp_path, monkeypatch) -> None:
    merged = tmp_path / "merged.rrd"
    merged.write_bytes(b"model-and-telemetry")
    recorder = gateway_app.runtime.recording
    token = recorder.playback_token("sample")
    monkeypatch.setattr(recorder, "playback_file", lambda recording_id, representation=None: merged)
    with TestClient(gateway_app.app) as client:
        response = client.get(f"/api/playback-files/sample.rrd?token={token}")
        assert response.status_code == 200
        assert response.content == b"model-and-telemetry"
        assert response.headers["access-control-allow-origin"] == "*"
        assert client.get("/api/playback-files/sample.rrd?token=wrong").status_code == 401


def test_prepare_playback_reports_backend_errors_before_opening_viewer(monkeypatch) -> None:
    recorder = gateway_app.runtime.recording
    monkeypatch.setattr(
        recorder,
        "playback_file",
        lambda recording_id, representation=None: (_ for _ in ()).throw(RuntimeError("visual model asset is invalid")),
    )
    with TestClient(gateway_app.app) as client:
        client.post("/api/auth/login", json={"code": gateway_app.auth.access_code})
        response = client.get("/api/recordings/sample/prepare-playback?representation=visual")
        assert response.status_code == 503
        assert response.json()["detail"] == "visual model asset is invalid"


def test_static_global_cloud_endpoint_only_serves_the_fixed_map_file(tmp_path, monkeypatch) -> None:
    cloud = tmp_path / "global_cloud_cleaned.pcd"
    cloud.write_text("# .PCD\n", encoding="utf-8")
    monkeypatch.setattr(gateway_app, "STATIC_GLOBAL_CLOUD", cloud)
    assert global_cloud().path == cloud


def test_robot_snapshot_is_safe_in_dry_run() -> None:
    snapshot = command("robot", "snapshot", {"include_lidar": False})
    assert snapshot == {"joint_state": None, "lidar": None, "sensors": None, "dry_run": True}


def test_robot_controller_switch_requires_explicit_confirmation() -> None:
    with pytest.raises(HTTPException) as error:
        command("robot", "switch_controller", {"controller": "CHASSIS_TWIST_CTRL"})
    assert error.value.status_code == 422
    result = command("robot", "switch_controller", {
        "controller": "CHASSIS_TWIST_CTRL", "confirmed": True,
    })
    assert result["dry_run"] is True


def test_lidar_stream_config_validates_actual_transmission_limit_and_crop() -> None:
    result = command("robot", "configure_lidar_stream", {
        "enabled": True,
        "max_points": 15000,
        "crop": {"half_x": 2.0, "half_y": 3.0, "half_z": 1.5},
    })
    assert result["max_points"] == 15000
    assert result["crop"] == {"half_x": 2.0, "half_y": 3.0, "half_z": 1.5}

    with pytest.raises(HTTPException) as error:
        command("robot", "configure_lidar_stream", {"max_points": 15001})
    assert error.value.status_code == 422


def test_motion_plan_preview_is_plan_only() -> None:
    result = command("motion", "plan_preview", {
        "chain": "left_arm",
        "target_pose": [0.4, 0.2, 0.8, 0, 0, 0, 1],
        "collision_check": True,
        "environment_collision_check": True,
        "obstacles": [],
    })
    assert result["plan_only"] is True
    assert result["sdk_planner_called"] is False
    assert result["request"]["frame_id"] == "base_link"


def test_motion_preview_accepts_current_end_effector_offset_without_execution() -> None:
    result = command("motion", "plan_preview", {
        "chain": "right_arm",
        "target_pose": [0.4, -0.2, 0.8, 0, 0, 0, 1],
        "target_mode": "current_offset",
        "position_offset": [0.03, 0.0, -0.02],
    })
    assert result["dry_run"] is True
    assert result["request"]["target_mode"] == "current_offset"
    assert result["request"]["position_offset"] == [0.03, 0.0, -0.02]


def test_motion_execution_requires_a_confirmation() -> None:
    request = MotionExecuteRequest(plan_id="plan", confirmed=False)
    assert request.confirmed is False
    with pytest.raises(HTTPException) as error:
        command("motion", "execute_plan", {"plan_id": "plan", "confirmed": False})
    assert error.value.status_code == 422


def test_simple_controls_require_explicit_confirmation() -> None:
    assert command("navigation", "base_velocity", {"linear": [0.1, 0, 0], "angular": [0, 0, 0]})["dry_run"] is True


def test_base_velocity_allows_joystick_limits_only() -> None:
    result = command("robot", "base_velocity", {"linear": [0.75, 0, 0], "angular": [0, 0, 1.0], "confirmed": True})
    assert result["dry_run"] is True
    with pytest.raises(HTTPException) as error:
        command("robot", "base_velocity", {"linear": [0.76, 0, 0], "angular": [0, 0, 0], "confirmed": True})
    assert error.value.status_code == 422


def test_workspace_stop_commands_are_available_in_dry_run() -> None:
    assert command("robot", "emergency_stop", {})["status"] == "EMERGENCY_STOP"
    assert command("motion", "stop_controller", {"group": "all"})["status"] == "CONTROLLER_STOPPED"


def test_move_straight_requires_confirmation_and_uses_relative_target() -> None:
    result = command("navigation", "move_straight_to", {"x": 0.4, "y": -0.2, "yaw": 0.1, "confirmed": True})
    assert result["dry_run"] is True
    assert result["target"]["x"] == 0.4
    with pytest.raises(HTTPException) as error:
        command("navigation", "move_straight_to", {"x": 0.1})
    assert error.value.status_code == 422
    assert command("motion", "move_end_effector_offset", {"arm": "left_arm", "dx": 0.01, "dy": 0, "dz": 0})["dry_run"] is True


def test_navigation_sdk_workflows_validate_and_dispatch_in_dry_run() -> None:
    v2 = command("navigation", "navigate_to_goal_v2", {
        "x": 1, "y": 2, "max_velocity": [0.4, 0.3, 0.5], "omni_plan": True,
    })
    assert v2["accepted"] is True
    assert v2["dry_run"] is True
    assert v2["task_id"]

    target = command("navigation", "set_navigation_target", {
        "x": 0.5, "y": -0.2, "speed_ratio": 0.6,
    })
    assert target["target"]["x"] == 0.5

    reachable = command("navigation", "check_path_reachability", {
        "goal": [1, 2, 0, 0, 0, 0, 1],
    })
    assert reachable == {
        "reachable": None,
        "dry_run": True,
        "detail": "dry-run cannot evaluate planner reachability",
    }


def test_navigation_route_and_velocity_require_confirmation() -> None:
    route = {"poses": [[0, 0, 0, 0, 0, 0, 1], [1, 0, 0, 0, 0, 0, 1]]}
    with pytest.raises(HTTPException) as error:
        command("navigation", "navigate_through_waypoints", route)
    assert error.value.status_code == 422
    result = command("navigation", "navigate_along_trajectory", {**route, "confirmed": True})
    assert result["pose_count"] == 2
    assert result["mode"] == "trajectory"

    with pytest.raises(HTTPException) as error:
        command("navigation", "navigate_with_velocity", {"vx": 0.2, "duration": 1})
    assert error.value.status_code == 422
    velocity = command("navigation", "navigate_with_velocity", {
        "vx": 0.2, "duration": 1, "confirmed": True,
    })
    assert velocity["velocity"]["vx"] == 0.2
