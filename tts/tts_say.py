#!/usr/bin/env python3
"""Local Piper TTS -> Galbot speaker (16k mono s16le PCM)."""
import os, subprocess, sys, tempfile, time
from galbot_sdk.g1 import GalbotRobot

PIPER = "/home/galbot/piper/piper/piper"
MODEL = "/home/galbot/piper/voices/zh_CN-huayan-medium.onnx"

def main():
    text = sys.argv[1] if len(sys.argv) > 1 else "你好，我是Galbot。"
    volume = float(sys.argv[2]) if len(sys.argv) > 2 else 80.0
    wav = tempfile.mktemp(suffix=".wav")
    pcm = tempfile.mktemp(suffix=".pcm")
    try:
        subprocess.run([PIPER, "--model", MODEL, "--output_file", wav],
                       input=text.encode("utf-8"), check=True,
                       stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        subprocess.run(["ffmpeg", "-y", "-i", wav, "-ar", "16000", "-ac", "1", "-f", "s16le", pcm],
                       check=True, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        data = open(pcm, "rb").read()
        robot = GalbotRobot()
        if not robot.init():
            raise RuntimeError("GalbotRobot init failed")
        try:
            robot.set_volume(max(0.0, min(100.0, volume)))
            for i in range(0, len(data), 2560):
                if not robot.write_audio_stream_output(data[i:i+2560], "tts_say"):
                    raise RuntimeError("audio write failed at %d" % i)
                time.sleep(0.05)
            time.sleep(0.8)
            robot.stop_audio_stream_output("tts_say")
        finally:
            robot.request_shutdown(); robot.wait_for_shutdown(); robot.destroy()
        print("TTS_OK", len(data))
    finally:
        for p in (wav, pcm):
            try: os.unlink(p)
            except OSError: pass

if __name__ == "__main__":
    main()
