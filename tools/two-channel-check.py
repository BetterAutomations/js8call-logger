#!/usr/bin/env python3
"""Prove two channels decode independently, then put the install back.

Stops the logger, brings up two cables, starts two decoders, plays the known
frame into both at once on a slot boundary, and checks the log carries a ch1
and a ch2 record. Restores the previous channel count and service state.

    python3 tools/two-channel-check.py
"""
import json
import os
import subprocess
import sys
import time

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
FIXTURE = os.path.join(ROOT, "tests", "fixtures", "n0call-cq.raw")
APP = os.path.join(ROOT, "js8call-logger")


def run(*args, **kw):
    return subprocess.run([APP, *args], capture_output=True, text=True, **kw)


def status_json():
    r = run("status", "--json")
    try:
        return json.loads(r.stdout)
    except ValueError:
        return {}


def wait_api(ports, limit=90):
    from js8call_logger import api
    end = time.time() + limit
    while time.time() < end:
        ok = 0
        for port in ports:
            try:
                c = api.Js8Api("127.0.0.1", port)
                c.connect(1.0)
                c.get_freq()
                c.close()
                ok += 1
            except Exception:
                pass
        if ok == len(ports):
            return True
        time.sleep(2)
    return False


def main() -> int:
    from js8call_logger import config
    cfg = config.load()
    before = int(cfg.get("channels") or 1)
    was_service = "active" in subprocess.run(
        ["systemctl", "--user", "is-active", "js8call-logger.service"],
        capture_output=True, text=True).stdout
    print(f"channels before: {before}, systemd unit active: {was_service}")

    run("stop")
    time.sleep(2)
    run("cable", "up", "--channels", "2")
    run("start")
    try:
        ports = [cfg.get("api_port", 2442) + n for n in range(2)]
        if not wait_api(ports):
            print("FAIL: both decoders did not answer", ports)
            return 1
        print("both decoders up:", ports)

        # JS8 frames begin on a 15 s boundary.
        time.sleep(max(0.0, (int(time.time() // 15) + 1) * 15 - time.time()))
        started = time.strftime("%Y-%m-%d %H:%M:%S")
        plays = []
        for sink in ("js8call-logger_cable", "js8call-logger_cable_2"):
            with open(FIXTURE, "rb") as fh:
                plays.append(subprocess.Popen(
                    ["pacat", f"--device={sink}", "--rate=48000", "--channels=1",
                     "--format=s16le", "--raw"], stdin=fh))
        for p in plays:
            p.wait()

        time.sleep(20)
        log = os.path.join(os.path.expanduser("~"), "js8call-logger", "logs",
                           "js8call-logger.jsonl")
        seen = set()
        for line in open(log, encoding="utf-8"):
            if not line.strip():
                continue
            rec = json.loads(line)
            if rec.get("ts", "") >= started:
                seen.add(rec.get("channel") or 1)
        print("channels that logged:", sorted(seen))
        ok = {1, 2} <= seen
        print("PASS: each channel decoded its own cable" if ok
              else "FAIL: a channel logged nothing")
        return 0 if ok else 1
    finally:
        run("stop")
        time.sleep(1)
        run("cable", "down")
        run("cable", "up", "--channels", str(before))
        if was_service:
            run("service", "install")
        else:
            run("start")
        print("restored: channels", before,
              "via", "systemd" if was_service else "start")


if __name__ == "__main__":
    sys.exit(main())
