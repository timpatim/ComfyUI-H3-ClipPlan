#!/usr/bin/env python
"""Command-line helper for H3 Clip Plan files (no ComfyUI needed).

    python plan_tool.py check my_plan.json [--song-duration 99.4]
    python plan_tool.py grid 22.5 40.1 51            nearest legal cut times
    python plan_tool.py frames                       legal clip lengths
"""
import json
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import core  # noqa: E402


def check(path, song_duration=None):
    with open(path, "r", encoding="utf-8-sig") as handle:
        try:
            raw = json.load(handle)
        except json.JSONDecodeError as exc:
            print(f"ERROR  not valid JSON: {exc}")
            return 1
    errors, warnings = core.validate(raw, song_duration)
    for warning in warnings:
        print("WARN  ", warning)
    for error in errors:
        print("ERROR ", error)
    if errors:
        print(f"\n{len(errors)} error(s): the plan cannot be used yet.")
        return 1
    print(core.format_timeline(core.normalize(raw)))
    print("\nOK: the plan is valid." + (f" ({len(warnings)} warning(s))" if warnings else ""))
    return 0


def grid(values):
    for value in values:
        seconds = float(value)
        n = max(0, round((seconds * core.FPS - 5) / 17))
        options = [(5 + 17 * k) / core.FPS for k in (max(0, n - 1), n, n + 1)]
        print(f"{seconds:8.3f} s -> " + "  ".join(f"{o:.3f}" for o in sorted(set(options))))
    return 0


def frames():
    for value in range(56, core.SOFT_MAX_FRAMES + 1, 17):
        print(f"{value:4d} frames = {value / core.FPS:6.3f} s  (adds {(value - core.DEFAULT_CONTEXT) / core.FPS:6.3f} s after a 39-frame overlap)")
    return 0


def main(argv):
    if len(argv) >= 2 and argv[0] == "check":
        duration = None
        if "--song-duration" in argv:
            duration = float(argv[argv.index("--song-duration") + 1])
        return check(argv[1], duration)
    if len(argv) >= 2 and argv[0] == "grid":
        return grid(argv[1:])
    if argv[:1] == ["frames"]:
        return frames()
    print(__doc__)
    return 2


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
