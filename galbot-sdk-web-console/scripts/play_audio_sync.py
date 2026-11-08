#!/usr/bin/env python3
"""Stream a 16 kHz mono s16le PCM file to the G1 speaker."""
from __future__ import annotations

import os
import sys
import time

from galbot_sdk.g1 import GalbotRobot


def main() -> int:
    if len(sys.argv) < 2:
        raise SystemExit("usage: play_audio.py <pcm> [volume]")
    path = sys.argv[1]
    volume = float(sys.argv[2]) if len(sys.argv) > 2 else 55.0
    data = open(path, "rb").read()
    robot = GalbotRobot()
    if not robot.init():
        raise SystemExit("GalbotRobot.init() failed")
    try:
        robot.set_volume(max(0.0, min(100.0, volume)))
        print(f"READY {time.time():.6f}", flush=True)
        deadline = time.time() + 8.0
        while not os.path.exists("/tmp/jingju_bgm_start"):
            if time.time() > deadline:
                break
            time.sleep(0.02)
        print(f"AUDIO_START {time.time():.6f}", flush=True)
        for offset in range(0, len(data), 2560):
            chunk = data[offset:offset + 2560]
            if not robot.write_audio_stream_output(chunk, "bgm"):
                raise RuntimeError(f"audio write failed at {offset}")
            time.sleep(0.08)
        time.sleep(0.4)
        robot.stop_audio_stream_output("bgm")
        print("AUDIO_DONE", flush=True)
    finally:
        robot.request_shutdown()
        robot.wait_for_shutdown()
        robot.destroy()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
