import asyncio
import json
import os
import struct
import threading
from concurrent.futures import ThreadPoolExecutor
from contextlib import asynccontextmanager
from dataclasses import asdict
from pathlib import Path
from typing import Any

from fastapi import FastAPI, HTTPException, Query, Request, WebSocket, WebSocketDisconnect
from fastapi.responses import FileResponse, JSONResponse, Response
from fastapi.staticfiles import StaticFiles

from ..api.schemas import NavigationGoalRequest, PoseRequest, RecordingArmRequest, RecordingConfirmRequest
from ..services.runtime import ConsoleRuntime
from ..services.core import CoreService
from ..geometry.rerun_robot import robot_model_recording_path
from .commands import dispatch_command
from .auth import AuthManager, SESSION_COOKIE

FRONTEND_ROOT = Path(__file__).resolve().parents[3] / "frontend"
ASSETS_ROOT = Path(__file__).resolve().parents[3] / "assets"
STATIC_GLOBAL_CLOUD = Path("/var/maps/cur/global_cloud_cleaned.pcd")
runtime = ConsoleRuntime(model=os.getenv("GALBOT_MODEL", "g1"), live=os.getenv("GALBOT_LIVE", "0").lower() in {"1", "true", "yes", "on"})
core = CoreService(runtime)
runtime.recording.static_map = STATIC_GLOBAL_CLOUD
# Camera SDK calls may wait indefinitely when a physical camera is absent.
# Keep them out of asyncio's shared executor so they cannot starve navigation,
# robot-state, or WebSocket event work.  One worker is enough per source.
camera_executor = ThreadPoolExecutor(max_workers=5, thread_name_prefix="galbot-camera")
auth = AuthManager()
rerun_viewer_started = False
rerun_viewer_lock = threading.Lock()


@asynccontextmanager
async def lifespan(_: FastAPI):
    print(f"Galbot SDK Web Console access code: {auth.access_code}", flush=True)
    try:
        runtime.initialize()
    except Exception as error:  # keep the static console available for diagnosis
        runtime.initialization_error = str(error)
        print(f"Galbot SDK runtime initialization failed: {error}", flush=True)
    try:
        yield
    finally:
        await runtime.close()


app = FastAPI(title="Galbot SDK Web Console Gateway", lifespan=lifespan)
app.mount("/frontend", StaticFiles(directory=FRONTEND_ROOT), name="frontend")
app.mount("/assets", StaticFiles(directory=ASSETS_ROOT), name="assets")


@app.middleware("http")
async def disable_frontend_cache(request: Request, call_next):
    public_api_paths = {"/api/auth/status", "/api/auth/login", "/api/scene/bootstrap"}
    public_model_asset = request.url.path.startswith("/api/recording-assets/") and request.url.path.endswith(".rrd")
    signed_playback = False
    if request.url.path.startswith("/api/recordings/") and request.url.path.endswith("/file"):
        recording_id = request.url.path.removeprefix("/api/recordings/").removesuffix("/file").strip("/")
        signed_playback = runtime.recording.validate_playback_token(recording_id, request.query_params.get("token", ""))
    elif request.url.path.startswith("/api/recording-files/") and request.url.path.endswith(".rrd"):
        recording_id = request.url.path.removeprefix("/api/recording-files/").removesuffix(".rrd").strip("/")
        signed_playback = runtime.recording.validate_playback_token(recording_id, request.query_params.get("token", ""))
    elif request.url.path.startswith("/api/playback-files/") and request.url.path.endswith(".rrd"):
        recording_id = request.url.path.removeprefix("/api/playback-files/").removesuffix(".rrd").strip("/")
        signed_playback = runtime.recording.validate_playback_token(recording_id, request.query_params.get("token", ""))
    if request.url.path.startswith("/api/") and request.url.path not in public_api_paths and not signed_playback and not public_model_asset:
        if not auth.is_authenticated(request.cookies.get(SESSION_COOKIE)):
            return JSONResponse({"detail": "authentication required"}, status_code=401)
    response = await call_next(request)
    if request.url.path == "/" or request.url.path.startswith("/frontend/"):
        response.headers["Cache-Control"] = "no-store"
    if request.url.path.startswith("/assets/robots/") and request.url.path.endswith(".rrd"):
        response.headers["Access-Control-Allow-Origin"] = "*"
        response.headers["Access-Control-Expose-Headers"] = "Accept-Ranges, Content-Length, Content-Range"
    return response


@app.get("/api/auth/status")
def auth_status(request: Request) -> dict[str, bool]:
    return {"authenticated": auth.is_authenticated(request.cookies.get(SESSION_COOKIE))}


@app.post("/api/auth/login")
def auth_login(payload: dict[str, Any], response: Response) -> dict[str, bool]:
    if not auth.verify_code(str(payload.get("code", ""))):
        raise HTTPException(status_code=401, detail="invalid access code")
    response.set_cookie(
        SESSION_COOKIE,
        auth.create_session(),
        httponly=True,
        samesite="lax",
        secure=False,
        max_age=8 * 60 * 60,
    )
    return {"authenticated": True}


@app.post("/api/auth/logout")
def auth_logout(request: Request, response: Response) -> dict[str, bool]:
    auth.revoke(request.cookies.get(SESSION_COOKIE))
    response.delete_cookie(SESSION_COOKIE)
    return {"authenticated": False}


@app.get("/")
def index() -> FileResponse:
    return FileResponse(FRONTEND_ROOT / "console.html")


@app.get("/api/maps/global-cloud")
def global_cloud() -> FileResponse:
    if not STATIC_GLOBAL_CLOUD.is_file():
        raise HTTPException(status_code=404, detail="static global cloud is not available")
    return FileResponse(STATIC_GLOBAL_CLOUD, media_type="application/octet-stream", filename=STATIC_GLOBAL_CLOUD.name)


@app.get("/api/recording-assets/{model}.rrd")
@app.head("/api/recording-assets/{model}.rrd")
def recording_model_asset(model: str) -> FileResponse:
    if model != runtime.session.model:
        raise HTTPException(status_code=404, detail="robot model recording is not available")
    path = robot_model_recording_path(model)
    if not path.is_file():
        raise HTTPException(status_code=404, detail="robot model recording is not available")
    response = FileResponse(path, media_type="application/octet-stream")
    response.headers["Access-Control-Allow-Origin"] = "*"
    response.headers["Access-Control-Expose-Headers"] = "Accept-Ranges, Content-Length, Content-Range"
    return response


@app.options("/api/recording-assets/{model}.rrd")
def recording_model_asset_options(model: str) -> Response:
    if model != runtime.session.model:
        raise HTTPException(status_code=404, detail="robot model recording is not available")
    return Response(headers={
        "Access-Control-Allow-Origin": "*",
        "Access-Control-Allow-Methods": "GET, HEAD, OPTIONS",
        "Access-Control-Allow-Headers": "Range",
        "Access-Control-Max-Age": "600",
    })


@app.get("/api/capabilities")
def capabilities() -> dict[str, Any]:
    return runtime.capabilities()


@app.get("/api/scene/bootstrap")
def scene_bootstrap() -> dict[str, str]:
    return {"model": runtime.session.model}


@app.get("/api/recording/capabilities")
def recording_capabilities() -> dict[str, Any]:
    return runtime.recording.capabilities()


@app.get("/api/recording/status")
def recording_status() -> dict[str, Any]:
    return runtime.recording.status()


@app.post("/api/recording/arm")
def recording_arm(request: RecordingArmRequest) -> dict[str, Any]:
    try:
        return runtime.recording.arm(request.model_dump())
    except ValueError as error:
        raise HTTPException(status_code=422, detail=str(error)) from error
    except RuntimeError as error:
        raise HTTPException(status_code=409, detail=str(error)) from error


def _recording_transition(action: str, request: RecordingConfirmRequest) -> dict[str, Any]:
    if action in {"finish", "cancel"} and not request.confirmed:
        raise HTTPException(status_code=422, detail=f"{action} requires explicit confirmation")
    try:
        return getattr(runtime.recording, action)()
    except RuntimeError as error:
        raise HTTPException(status_code=409, detail=str(error)) from error


@app.post("/api/recording/start")
def recording_start() -> dict[str, Any]:
    return _recording_transition("start", RecordingConfirmRequest())


@app.post("/api/recording/pause")
def recording_pause() -> dict[str, Any]:
    return _recording_transition("pause", RecordingConfirmRequest())


@app.post("/api/recording/resume")
def recording_resume() -> dict[str, Any]:
    return _recording_transition("resume", RecordingConfirmRequest())


@app.post("/api/recording/finish")
def recording_finish(request: RecordingConfirmRequest) -> dict[str, Any]:
    return _recording_transition("finish", request)


@app.post("/api/recording/cancel")
def recording_cancel(request: RecordingConfirmRequest) -> dict[str, Any]:
    return _recording_transition("cancel", request)


@app.get("/api/recording/viewer")
def recording_viewer() -> dict[str, Any]:
    global rerun_viewer_started
    with rerun_viewer_lock:
        if not rerun_viewer_started:
            try:
                import rerun as rr
                rr.start_web_viewer_server(runtime.recording.viewer_port)
            except Exception as error:
                raise HTTPException(status_code=503, detail=f"failed to start Rerun Web Viewer: {error}") from error
            rerun_viewer_started = True
    return {"port": runtime.recording.viewer_port}


@app.get("/api/recordings")
def recordings() -> dict[str, Any]:
    return {"recordings": runtime.recording.recordings()}


@app.get("/api/recordings/{recording_id:path}/prepare-playback")
def prepare_recording_playback(
    recording_id: str,
    representation: str = Query(default="collision", pattern="^(visual|collision)$"),
) -> dict[str, Any]:
    """Build the merged playback file while errors can still reach the console UI."""
    try:
        path = runtime.recording.playback_file(recording_id, representation=representation)
    except FileNotFoundError as error:
        raise HTTPException(status_code=404, detail="recording not found") from error
    except RuntimeError as error:
        raise HTTPException(status_code=503, detail=str(error)) from error
    return {"ready": True, "size_bytes": path.stat().st_size}


@app.post("/api/recordings/import")
async def import_recording(request: Request, filename: str) -> dict[str, Any]:
    length = int(request.headers.get("content-length", "0") or 0)
    if length > 2 * 1024**3:
        raise HTTPException(status_code=413, detail="recording file exceeds the 2 GiB import limit")
    try:
        temporary, destination = runtime.recording.prepare_import(filename)
        size = 0
        with temporary.open("xb") as sink:
            async for chunk in request.stream():
                size += len(chunk)
                if size > 2 * 1024**3:
                    raise HTTPException(status_code=413, detail="recording file exceeds the 2 GiB import limit")
                sink.write(chunk)
        return runtime.recording.complete_import(temporary, destination)
    except ValueError as error:
        raise HTTPException(status_code=422, detail=str(error)) from error
    finally:
        if "temporary" in locals() and temporary.exists():
            temporary.unlink()


@app.get("/api/recordings/{recording_id:path}/file")
@app.head("/api/recordings/{recording_id:path}/file")
def recording_file(recording_id: str) -> FileResponse:
    try:
        path = runtime.recording.recording_file(recording_id)
    except FileNotFoundError as error:
        raise HTTPException(status_code=404, detail="recording not found") from error
    response = FileResponse(path, media_type="application/octet-stream", filename=path.name)
    response.headers["Access-Control-Allow-Origin"] = "*"
    response.headers["Access-Control-Expose-Headers"] = "Accept-Ranges, Content-Length, Content-Range"
    return response


@app.get("/api/recording-files/{recording_path:path}")
@app.head("/api/recording-files/{recording_path:path}")
def recording_viewer_file(recording_path: str) -> FileResponse:
    """Serve a recording from an extension-bearing URL Rerun can classify."""
    if not recording_path.lower().endswith(".rrd"):
        raise HTTPException(status_code=404, detail="recording not found")
    try:
        path = runtime.recording.recording_file(recording_path[:-4])
    except FileNotFoundError as error:
        raise HTTPException(status_code=404, detail="recording not found") from error
    response = FileResponse(path, media_type="application/octet-stream")
    response.headers["Access-Control-Allow-Origin"] = "*"
    response.headers["Access-Control-Expose-Headers"] = "Accept-Ranges, Content-Length, Content-Range"
    return response


@app.options("/api/recording-files/{recording_path:path}")
def recording_viewer_file_options(recording_path: str) -> Response:
    return Response(headers={
        "Access-Control-Allow-Origin": "*",
        "Access-Control-Allow-Methods": "GET, HEAD, OPTIONS",
        "Access-Control-Allow-Headers": "Range",
        "Access-Control-Max-Age": "600",
    })


@app.get("/api/playback-files/{recording_path:path}")
@app.head("/api/playback-files/{recording_path:path}")
def recording_playback_file(recording_path: str, representation: str | None = Query(default=None, pattern="^(visual|collision)$")) -> FileResponse:
    """Serve one RRD containing the shared model and selected telemetry."""
    if not recording_path.lower().endswith(".rrd"):
        raise HTTPException(status_code=404, detail="recording not found")
    try:
        path = runtime.recording.playback_file(recording_path[:-4], representation=representation)
    except FileNotFoundError as error:
        raise HTTPException(status_code=404, detail="recording not found") from error
    except RuntimeError as error:
        raise HTTPException(status_code=503, detail=str(error)) from error
    response = FileResponse(path, media_type="application/octet-stream")
    response.headers["Access-Control-Allow-Origin"] = "*"
    response.headers["Access-Control-Expose-Headers"] = "Accept-Ranges, Content-Length, Content-Range"
    response.headers["Cache-Control"] = "private, max-age=3600"
    return response


@app.options("/api/playback-files/{recording_path:path}")
def recording_playback_file_options(recording_path: str) -> Response:
    return Response(headers={
        "Access-Control-Allow-Origin": "*",
        "Access-Control-Allow-Methods": "GET, HEAD, OPTIONS",
        "Access-Control-Allow-Headers": "Range",
        "Access-Control-Max-Age": "600",
    })


@app.options("/api/recordings/{recording_id:path}/file")
def recording_file_options(recording_id: str) -> Response:
    return Response(headers={
        "Access-Control-Allow-Origin": "*",
        "Access-Control-Allow-Methods": "GET, HEAD, OPTIONS",
        "Access-Control-Allow-Headers": "Range",
        "Access-Control-Max-Age": "600",
    })


@app.delete("/api/recordings/{recording_id:path}")
def delete_recording(recording_id: str, request: RecordingConfirmRequest) -> dict[str, Any]:
    if not request.confirmed:
        raise HTTPException(status_code=422, detail="recording deletion requires explicit confirmation")
    try:
        runtime.recording.delete(recording_id)
    except FileNotFoundError as error:
        raise HTTPException(status_code=404, detail="recording not found") from error
    return {"deleted": recording_id}

@app.get("/api/config/services")
def config_services() -> dict[str, Any]:
    return {"services": runtime.configuration.services(), "preview": not runtime.live}

# Compatibility routes for the full service_config_web frontend.
@app.get("/api/services")
def legacy_config_services() -> dict[str, Any]:
    return config_services()

@app.get("/api/schema/{service}")
def legacy_config_schema(service: str) -> dict[str, Any]:
    return config_schema(service)

@app.get("/api/logs")
def config_logs(after: int = 0) -> dict[str, Any]:
    # Runtime logs are emitted by the hosting process; retain the UI contract.
    return {"lines": [], "next_id": after}

@app.get("/api/config/schema/{service}")
def config_schema(service: str) -> dict[str, Any]:
    try: return runtime.configuration.schema(service)
    except ValueError as error: raise HTTPException(status_code=404, detail=str(error)) from error

@app.post("/api/config/{service}")
def save_config(service: str, payload: dict[str, Any]) -> dict[str, Any]:
    changes = payload.get("changes")
    if not isinstance(changes, dict) or not changes: raise HTTPException(status_code=422, detail="changes is required")
    try:
        return runtime.configuration.save(service, changes)
    except ValueError as error:
        # ConfigurationService serializes field-level validation failures so
        # the reference UI can highlight each invalid editor.
        try:
            detail = json.loads(str(error))
            if isinstance(detail, dict) and "errors" in detail:
                return JSONResponse(status_code=422, content=detail)
        except (TypeError, ValueError, json.JSONDecodeError):
            pass
        raise HTTPException(status_code=422, detail=str(error)) from error



@app.get("/api/core/status")
def core_status() -> dict[str, Any]:
    return core.status()


@app.get("/api/core/actions")
def core_actions() -> dict[str, Any]:
    return {"actions": core.list_actions()}


@app.post("/api/core/actions/{name}")
def core_run_action(name: str, payload: dict[str, Any]) -> dict[str, Any]:
    try:
        return core.run_action(name, bool(payload.get("confirmed")))
    except ValueError as error:
        raise HTTPException(status_code=422, detail=str(error)) from error
    except RuntimeError as error:
        raise HTTPException(status_code=503, detail=str(error)) from error


@app.post("/api/core/sequence")
def core_run_sequence(payload: dict[str, Any]) -> dict[str, Any]:
    try:
        return core.run_sequence(payload.get("steps", []), bool(payload.get("confirmed")))
    except ValueError as error:
        raise HTTPException(status_code=422, detail=str(error)) from error
    except RuntimeError as error:
        raise HTTPException(status_code=503, detail=str(error)) from error

@app.get("/api/robot/state")
def get_state() -> dict[str, Any]:
    return asdict(runtime.navigation.snapshot())


@app.get("/api/robot/camera/{source}")
def get_camera_frame(source: str) -> Response:
    try:
        frame = runtime.robot.camera_frame(source)
    except ValueError as error:
        raise HTTPException(status_code=404, detail=str(error)) from error
    except RuntimeError as error:
        raise HTTPException(status_code=503, detail=str(error)) from error
    headers = {
        "X-Camera-Source": str(frame["source"]),
        "X-Camera-Kind": str(frame["kind"]),
        "X-Camera-Width": str(frame["width"]),
        "X-Camera-Height": str(frame["height"]),
        "X-Camera-Timestamp-Ns": str(frame["timestamp_ns"]),
    }
    if frame["kind"] == "depth":
        headers.update({
            "X-Depth-Encoding": str(frame["encoding"]),
            "X-Depth-Scale": str(frame["depth_scale"]),
            "X-Depth-Low": str(frame["depth_low"]),
            "X-Depth-High": str(frame["depth_high"]),
        })
    return Response(content=frame["data"], media_type=frame.get("mime_type", "application/octet-stream"), headers=headers)


@app.post("/api/navigation/go")
def navigate(request: NavigationGoalRequest) -> dict[str, Any]:
    return runtime.navigation.go(request.to_sdk_array(), frame_id=request.frame_id, collision_check=request.collision_check, timeout=request.timeout)


@app.post("/api/navigation/stop")
def stop() -> dict[str, Any]:
    return runtime.navigation.stop()


@app.get("/api/navigation/tasks")
def navigation_tasks() -> dict[str, Any]:
    return {"tasks": runtime.navigation.tasks()}


@app.get("/api/navigation/tasks/{task_id}/path")
def navigation_path(task_id: str) -> dict[str, Any]:
    return runtime.navigation.path_snapshot(task_id)


@app.post("/api/navigation/relocalize")
def relocalize(request: PoseRequest) -> dict[str, Any]:
    return runtime.navigation.relocalize(request.to_sdk_array(), frame_id=request.frame_id)


@app.post("/api/commands/{service}/{method}")
def command(service: str, method: str, payload: dict[str, Any]) -> dict[str, Any]:
    return dispatch_command(runtime, service, method, payload)


@app.websocket("/api/events")
async def events(websocket: WebSocket) -> None:
    if not auth.is_authenticated(websocket.cookies.get(SESSION_COOKIE)):
        await websocket.close(code=1008, reason="authentication required")
        return
    await websocket.accept()
    try:
        async for event in runtime.events.subscribe():
            await websocket.send_json(event)
    except (WebSocketDisconnect, RuntimeError):
        return


CAMERA_SOURCE_IDS = {"left_rgb": 1, "left_depth": 2, "right_rgb": 3, "right_depth": 4, "head_fisheye": 5,
                      "surround_front_left": 6, "surround_front_right": 7, "surround_rear_left": 8,
                      "surround_rear_right": 9, "head_right": 10}


@app.websocket("/api/robot/camera-stream")
async def camera_stream(websocket: WebSocket) -> None:
    """Multiplex latest camera frames over one binary WebSocket per page."""
    if not auth.is_authenticated(websocket.cookies.get(SESSION_COOKIE)):
        await websocket.close(code=1008, reason="authentication required")
        return
    await websocket.accept()
    subscribed: dict[str, int] = {}
    tasks: dict[str, asyncio.Task[None]] = {}
    send_lock = asyncio.Lock()

    async def publish(source: str, fps: int) -> None:
        # Camera views are monitoring aids, so cap browser delivery at 10 FPS.
        # The SDK read still happens at this cadence to observe whether a new
        # hardware frame exists, but unchanged frames never cross the gateway.
        interval = 1 / max(1, min(fps, 10))
        read_future: asyncio.Future[dict[str, Any]] | None = None
        last_sent_timestamp_ns: int | None = None
        try:
            while source in subscribed:
                started = asyncio.get_running_loop().time()
                try:
                    # Do not create another SDK call after a timeout: the
                    # original worker could still be blocked in the driver.
                    # Reusing its future bounds blocked calls to one per source.
                    if read_future is None:
                        read_future = asyncio.get_running_loop().run_in_executor(
                            camera_executor, runtime.robot.camera_frame, source, last_sent_timestamp_ns,
                        )
                    frame = await asyncio.wait_for(asyncio.shield(read_future), timeout=1.5)
                    read_future = None
                    timestamp_ns = int(frame["timestamp_ns"])
                    if not frame.get("unchanged"):
                        kind = 1 if frame["kind"] == "depth" else 0
                        header = struct.pack(
                            "!4sBBHHQfHH", b"GBC1", CAMERA_SOURCE_IDS[source], kind,
                            min(int(frame["width"]), 65535), min(int(frame["height"]), 65535),
                            timestamp_ns, float(frame.get("depth_scale", 0)),
                            min(int(frame.get("depth_low", 0)), 65535), min(int(frame.get("depth_high", 0)), 65535),
                        )
                        async with send_lock:
                            await websocket.send_bytes(header + frame["data"])
                        last_sent_timestamp_ns = timestamp_ns
                except asyncio.TimeoutError:
                    async with send_lock:
                        await websocket.send_json({"type": "camera_error", "source": source, "detail": "camera frame timed out; retrying without affecting robot controls"})
                    await asyncio.sleep(1)
                except Exception as error:
                    read_future = None
                    async with send_lock:
                        await websocket.send_json({"type": "camera_error", "source": source, "detail": str(error)})
                    await asyncio.sleep(1)
                elapsed = asyncio.get_running_loop().time() - started
                await asyncio.sleep(max(0, interval - elapsed))
        except (asyncio.CancelledError, WebSocketDisconnect):
            return

    try:
        while True:
            message = await websocket.receive_json()
            source = str(message.get("source", ""))
            if source not in CAMERA_SOURCE_IDS:
                await websocket.send_json({"type": "camera_error", "source": source, "detail": "unknown camera source"})
                continue
            if message.get("op") == "subscribe":
                subscribed[source] = int(message.get("fps", 10))
                if source not in tasks or tasks[source].done():
                    tasks[source] = asyncio.create_task(publish(source, subscribed[source]))
            elif message.get("op") == "unsubscribe":
                subscribed.pop(source, None)
                task = tasks.pop(source, None)
                if task: task.cancel()
    except (WebSocketDisconnect, RuntimeError):
        pass
    finally:
        for task in tasks.values(): task.cancel()
