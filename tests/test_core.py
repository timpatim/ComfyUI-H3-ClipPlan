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


if __name__ == "__main__":
    for name, fn in sorted(globals().items()):
        if name.startswith("test_") and callable(fn):
            fn()
            print("ok  ", name)
    print("all tests passed")
