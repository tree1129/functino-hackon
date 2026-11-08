from math import isfinite

from pydantic import BaseModel, Field, field_validator, model_validator


class PoseRequest(BaseModel):
    x: float
    y: float
    z: float = 0.0
    qx: float = 0.0
    qy: float = 0.0
    qz: float = 0.0
    qw: float = 1.0
    frame_id: str = Field("map", min_length=1)

    def to_sdk_array(self) -> list[float]:
        """Return the ArrayLike layout accepted by GalbotNavigation."""
        return [self.x, self.y, self.z, self.qx, self.qy, self.qz, self.qw]


class NavigationGoalRequest(PoseRequest):
    collision_check: bool = True
    timeout: float = Field(30.0, gt=0.0, le=300.0)


class NavigationGoalV2Request(NavigationGoalRequest):
    max_velocity: list[float] = Field(default_factory=lambda: [0.5, 0.5, 0.5], min_length=3, max_length=3)
    omni_plan: bool = False

    @field_validator("max_velocity")
    @classmethod
    def validate_max_velocity(cls, values: list[float]) -> list[float]:
        if not all(isfinite(float(value)) and 0 < float(value) <= 2.0 for value in values):
            raise ValueError("max_velocity must contain three values in (0, 2]")
        return values


class NavigationTargetRequest(PoseRequest):
    speed_ratio: float = Field(1.0, gt=0.0, le=1.0)
    collision_check: bool = True


class NavigationRouteRequest(BaseModel):
    poses: list[list[float]] = Field(min_length=2, max_length=64)
    frame_id: str = Field("map", min_length=1, max_length=128)
    speed_ratio: float = Field(1.0, gt=0.0, le=1.0)
    collision_check: bool = True
    confirmed: bool = False

    @field_validator("poses")
    @classmethod
    def validate_poses(cls, poses: list[list[float]]) -> list[list[float]]:
        if any(len(pose) != 7 or not all(isfinite(float(value)) for value in pose) for pose in poses):
            raise ValueError("each route pose must contain seven finite values")
        return poses


class NavigationVelocityRequest(BaseModel):
    vx: float = Field(0.0, ge=-0.75, le=0.75)
    vy: float = Field(0.0, ge=-0.75, le=0.75)
    vyaw: float = Field(0.0, ge=-1.0, le=1.0)
    duration: float = Field(1.0, gt=0.0, le=60.0)
    collision_check: bool = True
    confirmed: bool = False


class NavigationReachabilityRequest(BaseModel):
    goal: list[float] = Field(min_length=7, max_length=7)
    current: list[float] | None = Field(default=None, min_length=7, max_length=7)

    @field_validator("goal", "current")
    @classmethod
    def validate_pose_array(cls, values: list[float] | None) -> list[float] | None:
        if values is not None and not all(isfinite(float(value)) for value in values):
            raise ValueError("pose must contain finite values")
        return values


class RobotSnapshotRequest(BaseModel):
    include_lidar: bool = False


class ControllerSwitchRequest(BaseModel):
    controller: str = Field(min_length=1, max_length=128, pattern=r"^[A-Z][A-Z0-9_]*$")
    confirmed: bool = False


class LidarCropRequest(BaseModel):
    half_x: float = Field(gt=0.0, le=50.0)
    half_y: float = Field(gt=0.0, le=50.0)
    half_z: float = Field(gt=0.0, le=20.0)


class LidarStreamConfigRequest(BaseModel):
    enabled: bool = True
    max_points: int = Field(8000, ge=1000, le=15000)
    crop: LidarCropRequest | None = None


class MotionPlanPreviewRequest(BaseModel):
    chain: str = Field(pattern="^(left_arm|right_arm)$")
    target_pose: list[float] = Field(min_length=7, max_length=7)
    target_mode: str = Field("absolute", pattern="^(absolute|current_offset)$")
    position_offset: list[float] = Field(default_factory=lambda: [0.0, 0.0, 0.0], min_length=3, max_length=3)
    frame_id: str = Field("base_link", min_length=1)
    collision_check: bool = True
    environment_collision_check: bool = True
    obstacles: list[dict] = Field(default_factory=list, max_length=256)
    timeout_seconds: float = Field(20.0, gt=0.0, le=120.0)


class MotionObstacleRequest(BaseModel):
    obstacle_id: str = Field(min_length=1, max_length=128, pattern=r"^[A-Za-z0-9_.-]+$")
    obstacle_type: str = Field(pattern="^(box|sphere|cylinder)$")
    pose: list[float] = Field(min_length=7, max_length=7)
    scale: list[float] = Field(min_length=3, max_length=3)
    target_frame: str = Field("base_link", min_length=1, max_length=128)

    @field_validator("pose")
    @classmethod
    def validate_pose(cls, values: list[float]) -> list[float]:
        if not all(isfinite(float(value)) for value in values):
            raise ValueError("pose must contain finite values")
        return values

    @field_validator("scale")
    @classmethod
    def validate_scale(cls, values: list[float]) -> list[float]:
        if not all(isfinite(float(value)) for value in values):
            raise ValueError("scale must contain finite values")
        return values

    @model_validator(mode="after")
    def validate_shape_scale(self):
        if self.obstacle_type == "box" and any(value <= 0 for value in self.scale):
            raise ValueError("box scale must contain three positive values")
        if self.obstacle_type == "sphere" and not (self.scale[0] > 0 and self.scale[1:] == [0, 0]):
            raise ValueError("sphere scale must be [radius, 0, 0]")
        if self.obstacle_type == "cylinder" and not (self.scale[0] > 0 and self.scale[1] > 0 and self.scale[2] == 0):
            raise ValueError("cylinder scale must be [radius, height, 0]")
        return self


class MotionObstacleRemoveRequest(BaseModel):
    obstacle_id: str = Field(min_length=1, max_length=128)


class MotionExecuteRequest(BaseModel):
    plan_id: str = Field(min_length=1, max_length=128)
    confirmed: bool = False
    speed_rad_s: float = Field(0.15, gt=0.0, le=0.3)
    start_tolerance_rad: float = Field(0.05, gt=0.0, le=0.2)


class MotionOffsetRequest(BaseModel):
    arm: str = Field("left_arm", pattern="^(left_arm|right_arm)$")
    dx: float = Field(0.0, ge=-0.1, le=0.1)
    dy: float = Field(0.0, ge=-0.1, le=0.1)
    dz: float = Field(0.0, ge=-0.1, le=0.1)
    roll: float = Field(0.0, ge=-0.2, le=0.2)
    pitch: float = Field(0.0, ge=-0.2, le=0.2)
    yaw: float = Field(0.0, ge=-0.2, le=0.2)
    confirmed: bool = True


class BaseVelocityRequest(BaseModel):
    linear: list[float] = Field(min_length=3, max_length=3)
    angular: list[float] = Field(min_length=3, max_length=3)
    duration: float = Field(0.0, ge=0.0, le=2.0)
    confirmed: bool = True

    @field_validator("linear")
    @classmethod
    def validate_linear(cls, values: list[float]) -> list[float]:
        if any(abs(value) > 0.75 for value in values):
            raise ValueError("linear velocity must be within +/-0.75 m/s")
        return values

    @field_validator("angular")
    @classmethod
    def validate_angular(cls, values: list[float]) -> list[float]:
        if any(abs(value) > 1.0 for value in values):
            raise ValueError("angular velocity must be within +/-1.0 rad/s")
        return values


class MoveStraightRequest(BaseModel):
    x: float = Field(0.0, ge=-5.0, le=5.0)
    y: float = Field(0.0, ge=-5.0, le=5.0)
    yaw: float = Field(0.0, ge=-3.141592653589793, le=3.141592653589793)
    timeout: float = Field(8.0, gt=0.0, le=60.0)
    confirmed: bool = False


class RecordingArmRequest(BaseModel):
    sources: list[str] = Field(min_length=1, max_length=32)
    directory: str = Field("", max_length=128)
    filename: str | None = Field(default=None, max_length=128)
    sample_hz: float = Field(5.0, ge=0.2, le=30.0)
    camera_fps: int = Field(5, ge=1, le=10)
    representation: str = Field("visual", pattern="^(visual|collision)$")

    @field_validator("sources")
    @classmethod
    def validate_sources(cls, values: list[str]) -> list[str]:
        normalized = list(dict.fromkeys(value.strip() for value in values if value.strip()))
        if not normalized:
            raise ValueError("at least one recording source is required")
        return normalized

    @field_validator("directory")
    @classmethod
    def validate_directory(cls, value: str) -> str:
        if "\0" in value:
            raise ValueError("directory contains an invalid character")
        normalized = value.strip().replace("\\", "/").strip("/")
        if any(character not in "abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789_./-" for character in normalized):
            raise ValueError("directory may only contain safe relative path characters")
        return normalized

    @field_validator("filename")
    @classmethod
    def validate_filename(cls, value: str | None) -> str | None:
        if value is None or not value.strip():
            return None
        name = value.strip()
        if name.lower().endswith(".rrd"):
            name = name[:-4]
        if not name or any(character not in "abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789_.-" for character in name):
            raise ValueError("filename may only contain letters, numbers, dot, dash and underscore")
        return name


class RecordingConfirmRequest(BaseModel):
    confirmed: bool = False
