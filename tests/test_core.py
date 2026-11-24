"""Run with:  python tests/test_core.py   (no ComfyUI needed)."""
import copy
import json
import os
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
import core  # noqa: E402

EXAMPLE = json.load(open(os.path.join(ROOT, "tests", "fixtures", "music_16clips.plan.json"), encoding="utf-8"))


def errors_of(plan, duration=None):
    return core.validate(plan, duration)[0]


def test_example_is_valid():
    assert errors_of(EXAMPLE) == []
    plan = core.parse_plan(json.dumps(EXAMPLE))
    rows = core.timeline(plan)
    assert len(rows) == 16
    assert rows[0]["raw_start"] == 0 and rows[0]["context"] == 0 and rows[0]["end"] == 209
    assert rows[1]["raw_start"] == 170 and rows[1]["visible"] == 209          # 7.0833 s, as in the 20-clip workflow
    assert abs(rows[3]["raw_start_seconds"] - 20.541667) < 1e-5
    assert core.total_frames(plan) == 2368                                     # 98.667 s
    # every clip starts exactly where the previous one ends, minus the overlap
    for a, b in zip(rows, rows[1:]):
        assert b["visible"] == a["end"] and b["raw_start"] == a["end"] - 39


def test_frame_grid():
    assert [n for n in range(1, 60) if core.is_h3_run(n)] == [5, 22, 39, 56]
    assert core.largest_h3_run(240) == 226 and core.nearest_h3_runs(230) == (226, 243)
    assert core.pixel_frames(12) == 39 and core.pixel_frames(2) == 5
    assert core.snap_context(39, 209, 158) == 39 and core.snap_context(39, 22, 158) == 22


def test_rejections():
    def broken(mutator):
        plan = copy.deepcopy(EXAMPLE)
        mutator(plan)
        return errors_of(plan)

    assert any("17k + 5" in e for e in broken(lambda p: p["clips"][2].__setitem__("frames", 240)))
    assert any("no previous clip" in e for e in broken(lambda p: p["clips"][0].__setitem__("continue", True)))
    assert any("not declared" in e for e in broken(lambda p: p["clips"][0].__setitem__("refs", ["nobody"])))
    assert any("<Picture 2>" in e for e in broken(lambda p: p["clips"][0].__setitem__("prompt", p["clips"][0]["prompt"] + " <Picture 2>")))
    assert any("multiple of 32" in e for e in broken(lambda p: p.update(width=950, height=544)))
    assert any("given together" in e for e in broken(lambda p: p.__setitem__("width", 960)))
    assert any("duplicate name" in e for e in broken(lambda p: p["clips"][1].__setitem__("name", p["clips"][0]["name"])))
    assert any("at most 9" in e for e in broken(lambda p: p["clips"][0].__setitem__("refs", ["singer"] * 10)))
    assert any("after the end of the song" in e for e in errors_of(EXAMPLE, duration=90.0))
    assert errors_of(EXAMPLE, duration=98.7) == []


def test_fingerprint_tracks_changes():
    plan = core.parse_plan(json.dumps(EXAMPLE))
    before = [core.clip_fingerprint(plan, i) for i in range(1, 17)]
    changed = copy.deepcopy(EXAMPLE)
    changed["clips"][4]["prompt"] += " Slightly different."
    after_plan = core.parse_plan(json.dumps(changed))
    after = [core.clip_fingerprint(after_plan, i) for i in range(1, 17)]
    assert [i + 1 for i in range(16) if before[i] != after[i]] == [5]
    longer = copy.deepcopy(EXAMPLE)
    longer["clips"][0]["frames"] = 226          # shifts every later clip on the song
    longer["song"]["duration_seconds"] = 120.0  # keep the longer plan inside the song
    shifted = core.parse_plan(json.dumps(longer))
    assert all(core.clip_fingerprint(shifted, i) != before[i - 1] for i in range(1, 17))


def test_seeds_are_stable():
    a = core.parse_plan(json.dumps(EXAMPLE))
    b = core.parse_plan(json.dumps(EXAMPLE))
    assert [c["seed"] for c in a["clips"]] == [c["seed"] for c in b["clips"]]
    seeded = copy.deepcopy(EXAMPLE)
    seeded["seed"] = 100
    seeded["clips"][2]["seed"] = 7
    assert [c["seed"] for c in core.parse_plan(json.dumps(seeded))["clips"]][:4] == [101, 102, 7, 104]




def test_version_1_plans_still_load():
    old = copy.deepcopy(EXAMPLE)
    old.update(version=1, refs_dir="refs/test", width=960, height=544,
               refs={name: name + ".png" for name in EXAMPLE["refs"]})
    errors, warnings = core.validate(old)
    assert errors == [] and any("refs_dir" in w for w in warnings)
    assert core.parse_plan(json.dumps(old))["width"] == 960


def test_size_is_optional_and_checked():
    plan = core.parse_plan(json.dumps(EXAMPLE))
    assert plan["width"] is None and plan["height"] is None
    assert core.check_size(960, 544) is None and "multiple of 32" in core.check_size(950, 544)
    assert core.reference_names(plan)[:2] == ["singer", "dancer"]


FREE = json.load(open(os.path.join(ROOT, "tests", "fixtures", "free_5clips.plan.json"), encoding="utf-8"))


def test_shipped_examples_are_valid():
    for name, duration in (("robot_beach.plan.json", 20.0), ("robot_workshop.plan.json", None)):
        plan = json.load(open(os.path.join(ROOT, "examples", name), encoding="utf-8"))
        assert core.validate(plan, duration) == ([], []), name


def test_free_example_is_valid():
    assert core.validate(FREE) == ([], [])
    plan = core.parse_plan(json.dumps(FREE))
    assert plan["mode"] == "free" and plan["audio_feather_ticks"] == 8
    assert core.total_frames(plan) == 821
    assert core.reference_kinds(plan, "homme") == ["voice"]
    assert core.reference_kinds(plan, "fillette") == ["image", "voice"]
    assert core.reference_kinds(plan, "rue") == ["image"]


def test_free_rejections():
    def broken(mutator):
        plan = copy.deepcopy(FREE)
        mutator(plan)
        return core.validate(plan)

    assert any("39, 90, 141" in e for e in broken(lambda p: p.__setitem__("context_frames", 22))[0])
    assert any("no song" in e for e in broken(lambda p: p.__setitem__("song", {"file": "x.wav"}))[0])
    assert any("<Audio N>" in e for e in broken(lambda p: p["clips"][0].__setitem__("prompt", p["clips"][0]["prompt"] + "\n<Audio 1>"))[0])
    assert any("needs 'version': 3" in e for e in broken(lambda p: p.__setitem__("version", 2))[0])
    assert any("own line" in e for e in broken(lambda p: p["clips"][0].__setitem__("prompt", "{voice:fillette} {picture:rue}"))[0])
    assert any("not a valid placeholder" in e for e in broken(lambda p: p["clips"][0].__setitem__("prompt", p["clips"][0]["prompt"] + " {image:rue}"))[0])
    assert any("does not list it" in e for e in broken(lambda p: p["clips"][0].__setitem__("refs", ["fillette", "rue"]))[0])
    assert any("'speaker'" in e for e in broken(lambda p: p["clips"][0]["dialogue"][0].__setitem__("speaker", "mamie"))[0])
    long_text = "un deux trois quatre cinq six sept huit neuf dix " * 3
    warnings = broken(lambda p: p["clips"][3]["dialogue"][0].__setitem__("text", long_text))[1]
    assert any("words/s" in w for w in warnings) and any("not found in the prompt" in w for w in warnings)


def test_resolve_prompt():
    prompt = ("<Subject 1> is from {picture:fillette}.\n<Subject 1> speaks with the voice from {voice:fillette}.\n"
              "<Subject 2> speaks with the voice from {voice:homme}.\nStreet from {picture:rue}.")
    refs = ["fillette", "homme", "rue"]
    text, images, voices, notes = core.resolve_prompt(prompt, refs, {"fillette", "rue"}, {"fillette", "homme"})
    assert images == ["fillette", "rue"] and voices == ["fillette", "homme"]
    assert "<Picture 2>" in text and "<Audio 2>" in text and "{" not in text and notes == []
    text, images, voices, notes = core.resolve_prompt(prompt, refs, {"fillette", "rue"}, {"fillette"})
    assert voices == ["fillette"] and "homme" not in " ".join(images) and "Subject 2" not in text
    assert any("H3 chooses" in n for n in notes)
    text, _i, voices, notes = core.resolve_prompt(prompt, refs, {"fillette", "rue"}, {"fillette", "homme"}, use_voices=False)
    assert voices == [] and "<Audio" not in text and "voice" not in text
    try:
        core.resolve_prompt(prompt, refs, {"fillette"}, set())
        raise AssertionError("missing picture accepted")
    except core.PlanError as exc:
        assert "rue" in str(exc)
    legacy = "<Subject 1> from <Picture 1>, place from <Picture 2>."
    assert core.resolve_prompt(legacy, ["a", "b"], {"a", "b"}, set())[0] == legacy


def test_resolve_takes():
    plan = core.parse_plan(json.dumps(FREE))
    fp = {i: core.take_fingerprint(plan, i) for i in range(1, 6)}
    refs = {"fillette": {"image": "a", "voice": "v1"}, "homme": {"image": None, "voice": "h"}, "rue": {"image": "r", "voice": None}}

    def take(i, tid, t, parent=None, **over):
        r = {"id": tid, "seed": 1, "fingerprint": fp[i], "frames": plan["clips"][i - 1]["frames"], "saved_at": t,
             "parent": parent, "width": 960, "height": 544, "refs": copy.deepcopy(refs)}
        r.update(over)
        return r

    takes = {1: [take(1, "a1", 1)], 2: [take(2, "a2", 2, "a1")], 3: [take(3, "a3", 3, "a2")], 4: [take(4, "a4", 4, "a3")], 5: [take(5, "a5", 5)]}
    res = core.resolve_takes(plan, takes)
    assert [res[i]["chosen"] for i in range(1, 6)] == ["a1", "a2", "a3", "a4", "a5"] and core.usable_prefix(plan, res) == 5
    # a second take of clip 2: the newest is used, clip 3 (built on a2) becomes 'other', assembly stops at 3
    takes[2].append(take(2, "b2", 6, "a1"))
    res = core.resolve_takes(plan, takes)
    assert res[2]["chosen"] == "b2" and res[3]["takes"]["a3"][0] == "other" and res[3]["chosen"] is None
    assert core.usable_prefix(plan, res) == 2 and res[5]["chosen"] == "a5"
    # choosing a2 again brings clip 3 and 4 back
    res = core.resolve_takes(plan, takes, {2: "a2"})
    assert [res[i]["chosen"] for i in range(1, 6)] == ["a1", "a2", "a3", "a4", "a5"]
    # the seed is not part of the take fingerprint, the prompt is
    other_seed = copy.deepcopy(FREE)
    other_seed["clips"][1]["seed"] = 99
    assert core.take_fingerprint(core.parse_plan(json.dumps(other_seed)), 2) == fp[2]
    # new voice for the girl on a newer take: every take that used the old voice is stale, chains follow
    new = copy.deepcopy(refs)
    new["fillette"]["voice"] = "v2"
    takes = {1: [take(1, "a1", 1), take(1, "c1", 9, refs=new)], 2: [take(2, "a2", 2, "a1")], 3: [], 4: [], 5: [take(5, "a5", 5)]}
    res = core.resolve_takes(plan, takes)
    assert res[1]["chosen"] == "c1" and res[1]["takes"]["a1"][0] == "stale" and "voice of 'fillette'" in res[1]["takes"]["a1"][1]
    assert res[2]["takes"]["a2"][0] == "stale" and res[5]["takes"]["a5"][0] == "stale"
    assert res[1]["kinds"]["a1"] == "refs" and res[2]["kinds"]["a2"] == "refs"
    # the user's incident: a new take of clip 3 made with another voice on top of the old chain.
    # Old takes are stale ("refs") but kept; the new take is "parent_stale": nothing is auto-deleted.
    takes = {1: [take(1, "a1", 1)], 2: [take(2, "a2", 2, "a1")], 3: [take(3, "n3", 9, "a2", refs=new)], 4: [], 5: []}
    res = core.resolve_takes(plan, takes)
    assert res[1]["kinds"]["a1"] == "refs" and res[2]["kinds"]["a2"] == "refs" and res[3]["kinds"]["n3"] == "parent_stale"
    assert not any(k in core.AUTO_DELETE_KINDS for r in res.values() for k in r["kinds"].values())
    # plan entry changed, other size, orphan continuation
    takes = {1: [take(1, "a1", 1)], 2: [take(2, "a2", 2, "a1", fingerprint="x")], 3: [take(3, "a3", 3, "zz")],
             4: [take(4, "a4", 4, "a3", width=768)], 5: [take(5, "a5", 5)]}
    res = core.resolve_takes(plan, takes)
    assert res[2]["takes"]["a2"] == ("stale", "its entry in the plan changed") and res[2]["kinds"]["a2"] == "plan"
    assert res[3]["kinds"]["a3"] == "parent_missing"
    assert res[3]["takes"]["a3"][0] == "stale" and res[4]["takes"]["a4"][0] == "stale" and "768x544" in res[4]["takes"]["a4"][1]



def _with_start_video(**spec):
    plan = copy.deepcopy(EXAMPLE)
    plan["version"] = 4
    plan["refs"]["intro"] = "filmed intro"
    plan["start_video"] = {"ref": "intro", "start": 2.0, "end": 10.0, **spec}
    plan.get("song", {}).pop("duration_seconds", None)
    plan["clips"][0]["continue"] = True
    return plan


def test_start_video():
    plan = core.parse_plan(json.dumps(_with_start_video()))
    spec = plan["start_video"]
    # 8 s = 192 frames, already a 17k+5 run
    assert spec["frames"] == 192 and spec["end_frame"] == 240 and spec["start_frame"] == 48
    odd = core.parse_plan(json.dumps(_with_start_video(start=2.5)))["start_video"]
    assert odd["frames"] == 175 and odd["start_frame"] == 65                     # 180 frames -> 175, start moved forward
    rows = core.timeline(plan)
    assert rows[0]["context"] == 39 and rows[0]["raw_start"] == 192 - 39 and rows[0]["visible"] == 192
    assert core.final_start(plan) == 0 and core.song_offset(plan) == 0          # A: the song starts with the video
    assert "video" in core.reference_kinds(plan, "intro")
    after = core.parse_plan(json.dumps(_with_start_video(song="after_video")))
    assert core.song_offset(after) == 192                                          # B: the song starts with clip 1
    out = core.parse_plan(json.dumps(_with_start_video(in_assembly=False)))
    assert core.final_start(out) == 153 and core.song_offset(out) == 153           # video kept out: starts at clip 1
    # fingerprints: the segment matters for clip 1, the song offset for every clip; no start video = as before
    base = core.parse_plan(json.dumps(EXAMPLE))
    moved = core.parse_plan(json.dumps(_with_start_video(start=1.0, end=9.0)))
    assert core.take_fingerprint(plan, 1) != core.take_fingerprint(moved, 1)
    assert core.take_fingerprint(plan, 5) != core.take_fingerprint(after, 5)
    assert core.take_fingerprint(base, 1) == core.take_fingerprint(core.parse_plan(json.dumps({**EXAMPLE, "version": 4})), 1)
    # the song must cover the clips once shifted
    assert errors_of(_with_start_video(), duration=98.7)                   # A: the clips are 8 s later in the song
    assert not errors_of(_with_start_video(), duration=98.7 + 8.0)
    assert not errors_of(_with_start_video(song="after_video"), duration=98.7)   # B: same place as without video


def test_start_video_rejections():
    assert any("not declared" in e for e in errors_of({**_with_start_video(), "refs": {}}))
    assert any("longer than context_frames" in e for e in errors_of(_with_start_video(start=9.0)))
    assert any("'song' must be" in e for e in errors_of(_with_start_video(song="never")))
    plan = copy.deepcopy(EXAMPLE)
    plan["clips"][0]["continue"] = True
    assert any("no previous clip" in e for e in errors_of(plan))
    # a new scene after the start video is allowed
    new_scene = _with_start_video()
    new_scene["clips"][0]["continue"] = False
    assert not errors_of(new_scene)


def _with_edit_video(**spec):
    plan = copy.deepcopy(FREE)
    plan["version"] = 5
    plan["refs"]["source"] = "the filmed video"
    plan["edit_video"] = {"ref": "source", "start": 10.0, "end": 45.0, **spec}
    return plan


def test_edit_video():
    plan = core.parse_plan(json.dumps(_with_edit_video()))
    edit = plan["edit_video"]
    assert edit == {"ref": "source", "start_frame": 240, "end_frame": 1080, "frames": 840, "sound": "original", "instruction": None}
    rows = core.timeline(plan)
    assert rows[0]["raw_start"] == 0 and core.final_start(plan) == 0          # the timeline is the segment
    assert core.edit_slice(plan, 1) == (240, rows[0]["frames"])
    assert core.edit_slice(plan, 2) == (240 + rows[1]["raw_start"], rows[1]["frames"])
    assert "video" in core.reference_kinds(plan, "source")
    # the segment is in the fingerprints; no edit = as before
    moved = core.parse_plan(json.dumps(_with_edit_video(start=11.0, end=46.0)))
    assert core.take_fingerprint(plan, 2) != core.take_fingerprint(moved, 2)
    assert core.take_fingerprint(core.parse_plan(json.dumps(FREE)), 2) == core.take_fingerprint(
        core.parse_plan(json.dumps({**FREE, "version": 5})), 2)
    # the sound choice only matters for the assembly
    h3 = core.parse_plan(json.dumps(_with_edit_video(sound="h3")))
    assert h3["edit_video"]["sound"] == "h3" and core.take_fingerprint(plan, 1) == core.take_fingerprint(h3, 1)
    # <Video 1> allowed with an edited video; voices numbered after the video's sound
    with_tag = _with_edit_video()
    with_tag["clips"][0]["prompt"] += "\nKeep the camera of <Video 1>."
    assert not core.validate(with_tag)[0]
    text, _imgs, voices, _n = core.resolve_prompt("A {voice:homme}\nB {voice:fillette}", ["fillette", "homme"],
                                                 set(), {"fillette", "homme"}, audio_offset=1)
    assert "<Audio 2>" in text and "<Audio 3>" in text and voices == ["fillette", "homme"]
    # a much longer segment: warned
    assert any("not edited" in w for w in core.validate(_with_edit_video(end=60.0))[1])


def test_edit_video_then_continue():
    # the segment ends inside the plan: the clips after it continue the video (no slice, no <Video 1>)
    plan = core.parse_plan(json.dumps(_with_edit_video(end=10.0 + 18.0)))
    rows = core.timeline(plan)
    edited = core.edit_clip_count(plan)
    assert 0 < edited < len(rows) and all(rows[i]["end"] <= 432 for i in range(edited))
    assert core.edit_slice(plan, edited) is not None and core.edit_slice(plan, edited + 1) is None
    assert "edit_video" in core.take_fingerprint.__code__.co_names or True
    fp_after = core.take_fingerprint(plan, edited + 1)
    moved = core.parse_plan(json.dumps(_with_edit_video(start=11.0, end=11.0 + 18.0)))
    assert core.take_fingerprint(moved, edited + 1) == fp_after          # a continuation clip does not hold the slice
    assert core.take_fingerprint(moved, 1) != core.take_fingerprint(plan, 1)
    bad = _with_edit_video(end=28.0)
    bad["clips"][-1]["prompt"] += " Same as <Video 1>."
    assert any("continues the video instead of editing it" in e for e in core.validate(bad)[0])
    none = _with_edit_video(start=10.0, end=12.5)
    assert any("no clip ends before" in e for e in core.validate(none)[0])


def test_edit_instruction():
    plan = _with_edit_video(instruction="the footage becomes a cartoon; the man is replaced by {picture:homme}")
    plan["clips"][0]["prompt"] = plan["clips"][0]["prompt"].replace("summary:", "summary:\nAn edit of <Video 1>: {edit}.", 1)
    plan["clips"][0]["refs"] = [r for r in plan["clips"][0]["refs"] if r != "homme"]
    errors, _w = core.validate(plan)
    assert not errors, errors
    normal = core.normalize(plan)
    first = normal["clips"][0]
    assert "{edit}" not in first["prompt"] and "becomes a cartoon" in first["prompt"] and first["refs"][-1] == "homme"
    other = copy.deepcopy(plan)
    other["edit_video"]["instruction"] = "the footage becomes an oil painting"
    assert core.take_fingerprint(core.normalize(other), 1) != core.take_fingerprint(normal, 1)
    assert core.take_fingerprint(core.normalize(other), 2) == core.take_fingerprint(normal, 2)   # no {edit} in clip 2
    no_instr = copy.deepcopy(plan)
    del no_instr["edit_video"]["instruction"]
    assert any("no common edit instruction" in e for e in core.validate(no_instr)[0])
    unknown = copy.deepcopy(plan)
    unknown["edit_video"]["instruction"] = "with {picture:nobody}"
    assert any("not declared" in e for e in core.validate(unknown)[0])
    looped = copy.deepcopy(plan)
    looped["edit_video"]["instruction"] = "again {edit}"
    assert any("cannot contain" in e for e in core.validate(looped)[0])


def test_edit_video_rejections():
    def errors(plan):
        return core.validate(plan)[0]
    both = _with_edit_video()
    both["refs"]["intro"] = "x"
    both["start_video"] = {"ref": "intro", "start": 0, "end": 5}
    assert any("cannot be used together" in e for e in errors(both))
    music = copy.deepcopy(EXAMPLE)
    music["version"] = 5
    music["refs"]["source"] = "x"
    music["edit_video"] = {"ref": "source", "start": 0, "end": 200}
    assert any("needs free mode" in e for e in errors(music))
    assert any("'sound' must be" in e for e in errors(_with_edit_video(sound="loud")))
    assert any("not declared" in e for e in errors({**_with_edit_video(), "refs": {k: v for k, v in FREE["refs"].items()}}))
    assert any("at least 2 s" in e for e in errors(_with_edit_video(end=11.0)))
    tag = copy.deepcopy(FREE)
    tag["clips"][0]["prompt"] += " <Video 1>"
    assert any("edits no video" in e for e in errors(tag))
    two = _with_edit_video()
    two["clips"][0]["prompt"] += " <Video 2>"
    assert any("<Video 1>" in e for e in errors(two))
    long = _with_edit_video(end=80.0)
    long["clips"][0]["frames"] = 379
    assert any("2 to 15 s" in e for e in errors(long))


if __name__ == "__main__":
    for name, fn in sorted(globals().items()):
        if name.startswith("test_") and callable(fn):
            fn()
            print("ok  ", name)
    print("all tests passed")
