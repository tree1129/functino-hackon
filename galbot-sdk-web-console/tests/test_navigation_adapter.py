from types import SimpleNamespace

from galbot_sdk_web_console.sdk.platforms.navigation import PythonNavigationAdapter
from galbot_sdk_web_console.services.navigation import NavigationService


class Pose:
    def __init__(self, values):
        self.values = list(values)


class WaypointParams:
    pass


class Waypoint:
    def __init__(self, pose, params):
        self.pose = pose
        self.params = params


class Navigation:
    def __init__(self):
        self.calls = []

    def get_active_navigation_task_id(self):
        return "active-task"

    def navigate_to_goal_v2(self, *args, **kwargs):
        self.calls.append(("v2", args, kwargs))
        return True, "RUNNING"

    def set_navigation_target(self, *args):
        self.calls.append(("target", args))
        return SimpleNamespace(task_id="target-task", request_sent=True)

    def navigate_through_waypoints(self, *args):
        self.calls.append(("waypoints", args))
        return SimpleNamespace(task_id="waypoint-task", request_sent=True)

    def navigate_along_trajectory(self, *args):
        self.calls.append(("trajectory", args))
        return SimpleNamespace(task_id="trajectory-task", request_sent=True)

    def navigate_with_velocity(self, *args):
        self.calls.append(("velocity", args))
        return False, "FAILED"

    def get_navigation_target_status(self, task_id):
        assert task_id == "target-task"
        return SimpleNamespace(status=SimpleNamespace(name="RUNNING"))


def make_adapter():
    navigation = Navigation()
    robot = SimpleNamespace(switch_controller=lambda controller: "SUCCESS")
    sdk = SimpleNamespace(
        G1ControllerName=SimpleNamespace(CHASSIS_POSE_CTRL="pose-controller"),
        ControlStatus=SimpleNamespace(SUCCESS="SUCCESS"),
        Pose=Pose,
        Waypoint=Waypoint,
        WaypointParams=WaypointParams,
    )
    platform = SimpleNamespace(
        model="g1",
        navigation_instance=navigation,
        require_navigation=lambda: (robot, navigation, sdk),
    )
    return PythonNavigationAdapter(platform), navigation


def test_sdk_navigation_commands_match_v1_10_signatures_and_keep_task_ids() -> None:
    adapter, navigation = make_adapter()
    pose = [1, 2, 0, 0, 0, 0, 1]

    v2 = adapter.navigate_to_goal_v2(pose, [0.4, 0.3, 0.5], "map", True, 12, False)
    assert v2["task_id"] == "active-task"
    assert v2["request_sent"] is True
    _, args, kwargs = navigation.calls[-1]
    assert args == (pose, [0.4, 0.3, 0.5])
    assert kwargs == {
        "pose_frame": "map", "enable_collision_check": True,
        "is_blocking": False, "timeout": 12.0, "omni_plan": False,
    }

    target = adapter.set_navigation_target(pose, "map", 0.6, True)
    assert target["task_id"] == "target-task"
    assert navigation.calls[-1][1][0].values == pose

    waypoint = adapter.navigate_through_waypoints([pose, pose], "map", True)
    assert waypoint["task_id"] == "waypoint-task"
    assert all(isinstance(item, Waypoint) for item in navigation.calls[-1][1][0])

    trajectory = adapter.navigate_along_trajectory([pose, pose], "map", 0.8, False)
    assert trajectory["task_id"] == "trajectory-task"
    assert all(isinstance(item, Pose) for item in navigation.calls[-1][1][0])


def test_tuple_navigation_results_preserve_acceptance_and_task_status() -> None:
    adapter, _ = make_adapter()
    result = adapter.navigate_with_velocity(0.2, 0.0, 0.1, 2.0, True)
    assert result["request_sent"] is False
    assert result["task_id"] is None
    assert adapter.navigation_target_status("target-task") == "RUNNING"


def test_live_service_never_invents_a_task_id_for_path_queries() -> None:
    navigation = SimpleNamespace(
        navigate_to_goal_v2=lambda *args: {
            "result": (True, "RUNNING"), "task_id": None, "request_sent": True,
        },
    )
    session = SimpleNamespace(initialized=True, platform=SimpleNamespace(navigation=navigation))
    service = NavigationService(session, live=True)
    result = service.go_v2(
        [1, 0, 0, 0, 0, 0, 1], max_velocity=[0.5, 0.5, 0.5],
        frame_id="map", collision_check=True, timeout=10, omni_plan=False,
    )
    assert result["accepted"] is True
    assert result["task_id"] is None
    assert service.path_snapshot() == {
        "task_id": None, "available": False, "revision": 0,
        "frame_id": "", "poses": [], "msg": "no navigation task",
    }


def test_navigation_service_keeps_recent_tasks_for_path_selection() -> None:
    session = SimpleNamespace(initialized=False, platform=SimpleNamespace())
    service = NavigationService(session, live=False)
    first = service.go_v2(
        [1, 0, 0, 0, 0, 0, 1], max_velocity=[0.5, 0.5, 0.5],
        frame_id="map", collision_check=True, timeout=10, omni_plan=False,
    )
    second = service.navigate_route(
        [[0, 0, 0, 0, 0, 0, 1], [1, 1, 0, 0, 0, 0, 1]],
        frame_id="map", speed_ratio=1, collision_check=True,
        trajectory=True, confirmed=True,
    )

    tasks = service.tasks()
    assert [task["task_id"] for task in tasks] == [second["task_id"], first["task_id"]]
    assert [task["mode"] for task in tasks] == ["trajectory", "goal"]
    assert all(task["created_ns"] > 0 for task in tasks)
