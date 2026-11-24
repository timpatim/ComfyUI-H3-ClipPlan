"""H3 Clip Plan - ComfyUI nodes.

One small graph renders one clip per queue run, driven by a JSON plan file.
The heavy lifting is delegated to nodes that already exist:

* MiniMaxH3ReferenceToVideo (ComfyUI core)      prompt + reference images -> conditioning, empty AV latent
* MiniMaxH3SongMaskedAVContext (MultiRef pack)   music_video mode: song slice + previous-clip prefix
* MiniMaxH3GeneratedAVMaskedContext (MultiRef)   free mode: previous-clip picture AND sound prefix
* MiniMaxH3StreamLiveMusicVideoToVHS (MultiRef)  final assembly, with the song or with the
                                                 sound track rebuilt from the generated clips
"""
from __future__ import annotations

import hashlib
import json
import logging
import os
import random
import shutil
import subprocess
import time
import uuid

import numpy as np
import torch
from PIL import Image, ImageOps

import folder_paths

from . import core

_LOG = logging.getLogger("h3_clip_plan")
CATEGORY = "conditioning/minimax/H3 Clip Plan"
OUTPUT_ROOT = "h3_clip_plan"
MULTIREF_HINT = "install or update the 'ComfyUI-H3-Motion-Context-MultiRef' node pack"


# --------------------------------------------------------------------------- helpers
def _node_class(name: str, hint: str):
    import nodes as comfy_nodes

    cls = comfy_nodes.NODE_CLASS_MAPPINGS.get(name)
    if cls is None:
        raise RuntimeError(f"H3 Clip Plan: node '{name}' is not available; {hint}")
    return cls


def _plan_path(plan_file: str) -> str:
    raw = str(plan_file).strip().strip('"')
    if not raw or raw == NO_PLAN:
        raise ValueError("H3 Clip Plan: no plan selected; choose a .json plan in the 'plan_file' field")
    path = raw if os.path.isabs(raw) else os.path.join(folder_paths.get_input_directory(), raw)
    if not os.path.isfile(path):
        raise FileNotFoundError(
            f"H3 Clip Plan: plan file not found: {path} (a relative path is resolved inside ComfyUI's input folder)"
        )
    return path


def _read_plan(plan_file: str):
    path = _plan_path(plan_file)
    with open(path, "r", encoding="utf-8-sig") as handle:
        text = handle.read()
    plan = core.parse_plan(text)
    for warning in core.validate(json.loads(text))[1]:
        _LOG.warning("H3 Clip Plan: %s", warning)
    return plan, path


def _clip_entry(plan, clip_index: int):
    count = len(plan["clips"])
    index = int(clip_index)
    if not 1 <= index <= count:
        raise ValueError(f"H3 Clip Plan: clip_index {index} is outside the plan (1 to {count})")
    return plan["clips"][index - 1], core.timeline(plan)[index - 1]


def _project_dir(plan, *parts) -> str:
    path = os.path.join(folder_paths.get_output_directory(), OUTPUT_ROOT, plan["project"], *parts)
    return path


# ---- takes: every render of a clip is kept as a "take" until it becomes stale or is pruned
#   output/h3_clip_plan/<project>/latents/clip_NNN/<take id>.pt    latent
#   output/h3_clip_plan/<project>/latents/clip_NNN/<take id>.json  signature (see core.resolve_takes)
#   output/h3_clip_plan/<project>/clips/clip_NNN_<name>_<take id>_00001.mp4   preview(s)
#   output/h3_clip_plan/<project>/selection.json                   take chosen for each clip
PREVIEW_EXTENSIONS = (".mp4", ".webm", ".mov", ".mkv", ".gif", ".png")


def _take_dir(plan, index: int) -> str:
    return _project_dir(plan, "latents", f"clip_{int(index):03d}")


def _take_paths(plan, index: int, take_id: str):
    base = os.path.join(_take_dir(plan, index), take_id)
    return base + ".pt", base + ".json"


def _list_takes(plan, index: int):
    folder = _take_dir(plan, index)
    records = []
    if not os.path.isdir(folder):
        return records
    for name in sorted(os.listdir(folder)):
        if not name.endswith(".json"):
            continue
        take_id = name[:-5]
        latent, meta = _take_paths(plan, index, take_id)
        if not os.path.isfile(latent):
            continue
        try:
            with open(meta, "r", encoding="utf-8") as handle:
                record = json.load(handle)
        except (OSError, ValueError):
            continue
        record["id"] = take_id
        records.append(record)
    return records


def _all_takes(plan):
    return {clip["index"]: _list_takes(plan, clip["index"]) for clip in plan["clips"]}


def _selection_file(plan) -> str:
    return _project_dir(plan, "selection.json")


def _read_selection(plan):
    try:
        with open(_selection_file(plan), "r", encoding="utf-8") as handle:
            data = json.load(handle)
        return {int(k): v for k, v in data.items() if v}
    except (OSError, ValueError):
        return {}


def _write_selection(plan, selection):
    path = _selection_file(plan)
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path + ".tmp", "w", encoding="utf-8") as handle:
        json.dump({str(k): v for k, v in sorted(selection.items())}, handle, indent=1)
    os.replace(path + ".tmp", path)


def _resolve(plan):
    return core.resolve_takes(plan, _all_takes(plan), _read_selection(plan))


def _preview_files(plan, index: int, take_id: str):
    folder = _project_dir(plan, "clips")
    if not os.path.isdir(folder):
        return []
    head, tail = f"clip_{int(index):03d}_", f"_{take_id}_"
    return sorted(
        os.path.join(folder, name) for name in os.listdir(folder)
        if name.startswith(head) and tail in name and name.lower().endswith(PREVIEW_EXTENSIONS)
    )


def _delete_take(plan, index: int, take_id: str):
    """Remove a take: its latent, its signature and its previews."""
    for path in list(_take_paths(plan, index, take_id)) + _preview_files(plan, index, take_id):
        try:
            os.remove(path)
        except FileNotFoundError:
            pass


def _delete_legacy_latents(plan):
    """Latents saved before takes existed (latents/clip_NNN.pt and .json): removed, as agreed."""
    folder = _project_dir(plan, "latents")
    removed = []
    if os.path.isdir(folder):
        for name in os.listdir(folder):
            path = os.path.join(folder, name)
            if os.path.isfile(path) and name.startswith("clip_") and name.endswith((".pt", ".json", ".pt.tmp")):
                os.remove(path)
                removed.append(name)
    return removed


def _load_take(plan, index: int, take_id: str):
    latent, _meta = _take_paths(plan, index, take_id)
    if not os.path.isfile(latent):
        raise FileNotFoundError(f"H3 Clip Plan: take {take_id} of clip {index} is missing ({latent})")
    data = torch.load(latent, map_location="cpu", weights_only=True)
    expected = plan["clips"][index - 1]["frames"]
    if int(data.get("frames", -1)) != expected:
        raise ValueError(
            f"H3 Clip Plan: take {take_id} of clip {index} has {data.get('frames')} frames but the plan now says "
            f"{expected}; render that clip again"
        )
    return {"samples": [data["video"], data["audio"]]}


def _digest(value) -> str:
    """Short content hash of an image tensor or an audio dict."""
    sha = hashlib.sha1()
    if isinstance(value, dict):
        sha.update(str(int(value.get("sample_rate", 0))).encode())
        value = value["waveform"]
    array = value.detach().to("cpu").contiguous().numpy() if hasattr(value, "detach") else np.asarray(value)
    sha.update(str(tuple(array.shape)).encode())
    sha.update(array.tobytes())
    return sha.hexdigest()[:16]


NO_PLAN = "(no .json plan in ComfyUI/input - use the button below)"
IMAGE_EXTENSIONS = (".png", ".jpg", ".jpeg", ".webp", ".bmp")
AUDIO_EXTENSIONS = (".wav", ".mp3", ".flac", ".ogg", ".m4a", ".aac", ".opus")
VIDEO_EXTENSIONS = (".mp4", ".mov", ".mkv", ".webm", ".avi", ".m4v")
NO_VIDEO = "(no video file in input)"
_SKIP_DIRS = {"3d", "clipspace", "pasted"}


def _input_files(extensions, max_depth=3):
    """Files of ComfyUI's input folder (and sub-folders) as relative posix paths."""
    root = folder_paths.get_input_directory()
    found = []
    for folder, dirs, files in os.walk(root):
        depth = os.path.relpath(folder, root).count(os.sep) + (0 if folder == root else 1)
        dirs[:] = [d for d in dirs if not d.startswith(".") and d not in _SKIP_DIRS] if depth < max_depth else []
        for name in files:
            if name.lower().endswith(extensions):
                found.append(os.path.relpath(os.path.join(folder, name), root).replace(os.sep, "/"))
    return sorted(found, key=str.lower)


NO_VOICE = "(no audio file in input)"


def _plan_choices():
    return _input_files((".json",)) or [NO_PLAN]


PLAN_INPUT = lambda: (_plan_choices(), {"tooltip": "JSON plan from ComfyUI's input folder. Use the button to pick or drop a file."})  # noqa: E731


def _load_image(path: str) -> torch.Tensor:
    with Image.open(path) as image:
        image = ImageOps.exif_transpose(image).convert("RGB")
        array = np.asarray(image, dtype=np.float32) / 255.0
    return torch.from_numpy(array)[None,]


def _load_audio(path: str):
    try:
        from comfy_extras.nodes_audio import load as comfy_load
        waveform, sample_rate = comfy_load(path)
    except ImportError:
        import torchaudio
        waveform, sample_rate = torchaudio.load(path)
    return {"waveform": waveform.unsqueeze(0) if waveform.ndim == 2 else waveform, "sample_rate": int(sample_rate)}


def _stereo(waveform):
    waveform = waveform[:1].to(torch.float32)
    if waveform.shape[1] == 1:
        return waveform.repeat(1, 2, 1)
    if waveform.shape[1] > 2:
        return waveform[:, :2]
    return waveform


def _resample(waveform, source_sr: int, target_sr: int):
    if int(source_sr) == int(target_sr):
        return waveform
    try:
        import torchaudio
        return torchaudio.functional.resample(waveform, int(source_sr), int(target_sr))
    except ImportError:
        length = int(round(waveform.shape[-1] * int(target_sr) / int(source_sr)))
        return torch.nn.functional.interpolate(waveform, size=length, mode="linear", align_corners=False)


def _fit_length(waveform, want: int):
    """Make a waveform exactly ``want`` samples long: tiny differences are stretched, others padded/cut."""
    have = int(waveform.shape[-1])
    if have == want:
        return waveform
    if want > 0 and abs(have - want) / want <= 0.005:
        return torch.nn.functional.interpolate(waveform, size=want, mode="linear", align_corners=False)
    if have > want:
        return waveform[..., :want]
    return torch.nn.functional.pad(waveform, (0, want - have))


def _prepare_voices(voices, audio_vae):
    """Cut the voice samples so that H3's limits hold: 2-15 s each, 15 s in all."""
    if not voices:
        return {}, []
    vae_sr = int(getattr(audio_vae, "audio_sample_rate", 32000))
    step = max(1, vae_sr // core.AUDIO_HZ)
    budget = core.VOICE_TOTAL_SECONDS / len(voices)
    prepared, notes = {}, []
    for number, (name, audio) in enumerate(voices):
        waveform = _resample(_stereo(audio["waveform"]), audio["sample_rate"], vae_sr)
        seconds = waveform.shape[-1] / vae_sr
        if seconds < core.VOICE_MIN_SECONDS:
            raise ValueError(
                f"H3 Clip Plan: the voice sample '{name}' lasts {seconds:.1f} s; MiniMax H3 needs at least "
                f"{core.VOICE_MIN_SECONDS:.0f} s per reference audio"
            )
        keep = int(min(seconds, budget) * vae_sr) // step * step
        if keep < waveform.shape[-1]:
            notes.append(f"voice '{name}' cut to its first {keep / vae_sr:.1f} s (limit 15 s for {len(voices)} voice(s))")
        prepared[f"ref_audio_{number}"] = {"waveform": waveform[..., :keep].contiguous(), "sample_rate": vae_sr}
    return prepared, notes


def _decode_audio(audio_vae, audio_latent):
    """Decode an H3 audio latent the same way as the MultiRef pack does."""
    waveform = audio_vae.decode(audio_latent).movedim(-1, 1)
    std = torch.std(waveform, dim=[1, 2], keepdim=True) * 5.0
    std[std < 1.0] = 1.0
    waveform = waveform / std
    rate = int(getattr(audio_vae, "audio_sample_rate_output", getattr(audio_vae, "audio_sample_rate", 44100)))
    return _stereo(waveform.detach().to("cpu", torch.float32)), rate


def _bank_entry(bank, name):
    """{'image': tensor or None, 'voice': audio or None, 'video': {'path', 'digest'} or None} for a reference name."""
    value = (bank or {}).get(name)
    if value is None:
        return {"image": None, "voice": None, "video": None}
    if isinstance(value, dict) and ("image" in value or "voice" in value or "video" in value):
        return {"image": value.get("image"), "voice": value.get("voice"), "video": value.get("video")}
    return {"image": value, "voice": None, "video": None}   # plain tensor from an older reference node


def _add_to_bank(references, name, kind, value, node_title):
    label = str(name).strip()
    bank = {key: _bank_entry(references, key) for key in (references or {})}
    entry = bank.get(label, {"image": None, "voice": None, "video": None})
    if entry[kind] is not None:
        raise ValueError(f"H3 Clip Plan: two '{node_title}' nodes use the name '{label}'")
    entry[kind] = value
    bank[label] = entry
    return bank


def _split_av(latent):
    samples = latent["samples"]
    if hasattr(samples, "unbind"):
        parts = list(samples.unbind())
    elif isinstance(samples, (tuple, list)):
        parts = list(samples)
    else:
        raise ValueError("H3 Clip Plan: expected a MiniMax H3 audio+video latent")
    if len(parts) < 2:
        raise ValueError("H3 Clip Plan: expected a joint H3 video+audio latent")
    video, audio = parts[0], parts[1]
    if video.ndim == 4:
        video = video.unsqueeze(0)
    if audio.ndim == 3:
        audio = audio.unsqueeze(0)
    return video, audio


def _mtime(path: str) -> float:
    try:
        return os.path.getmtime(path)
    except OSError:
        return 0.0


def _encode(clip, vae, audio_vae, prompt, width, height, frames, images, ref_image_size, voices=None,
            ref_video=None, ref_video_audio=None):
    try:
        from comfy_extras.nodes_minimax_h3 import MiniMaxH3ReferenceToVideo as r2v
    except Exception:  # noqa: BLE001 - fall back to the registry on any import problem
        r2v = _node_class("MiniMaxH3ReferenceToVideo", "update ComfyUI to a version with native MiniMax H3 support")
    refs = {f"ref_image_{i}": image for i, image in enumerate(images)}
    output = r2v.execute(
        clip=clip, vae=vae, audio_vae=audio_vae, prompt=prompt,
        width=int(width), height=int(height), length=int(frames),
        ref_image_size=ref_image_size, ref_images=refs, ref_audios=voices or None,
        ref_videos={"ref_video_1": ref_video} if ref_video is not None else None,
        ref_video_audios={"ref_video_audio_1": ref_video_audio} if ref_video is not None and ref_video_audio is not None else None,
    )
    result = getattr(output, "result", output)
    return result[0], result[1]


# ---- videos: read with ffmpeg (the one of VideoHelperSuite's imageio-ffmpeg, or on the PATH), one segment at a time
def _ffmpeg() -> str:
    path = os.environ.get("H3_FFMPEG", "").strip()
    if path:
        return path
    try:
        from imageio_ffmpeg import get_ffmpeg_exe
        return get_ffmpeg_exe()
    except Exception:  # noqa: BLE001 - imageio-ffmpeg missing or without a binary
        pass
    path = shutil.which("ffmpeg")
    if not path:
        raise RuntimeError("H3 Clip Plan: ffmpeg not found; install ComfyUI-VideoHelperSuite (it brings imageio-ffmpeg) "
                           "or put ffmpeg on the PATH")
    return path


def _file_digest(path: str) -> str:
    sha = hashlib.sha1()
    with open(path, "rb") as handle:
        for block in iter(lambda: handle.read(1 << 20), b""):
            sha.update(block)
    return sha.hexdigest()[:16]


def _read_video_frames(path: str, start_frame: int, frames: int, width: int, height: int) -> torch.Tensor:
    """``frames`` pictures of a video file from ``start_frame`` (24 fps timeline of the file), resized to cover
    width x height and cropped in the middle. Returns an IMAGE batch [frames, height, width, 3]."""
    command = [
        _ffmpeg(), "-v", "error", "-ss", f"{start_frame / core.FPS:.6f}", "-i", path, "-an",
        "-vf", f"fps={core.FPS},scale={width}:{height}:force_original_aspect_ratio=increase:flags=lanczos,"
               f"crop={width}:{height}",
        "-frames:v", str(int(frames)), "-f", "rawvideo", "-pix_fmt", "rgb24", "-",
    ]
    done = subprocess.run(command, capture_output=True, check=False)
    if done.returncode != 0:
        raise RuntimeError(f"H3 Clip Plan: cannot read the video {os.path.basename(path)}: "
                           f"{done.stderr.decode('utf-8', 'replace').strip()[-400:]}")
    size = width * height * 3
    got = len(done.stdout) // size
    if got < frames:
        raise ValueError(
            f"H3 Clip Plan: the video {os.path.basename(path)} has only {got} frames from "
            f"{start_frame / core.FPS:.3f} s, {frames} are needed; it ends before the segment of the plan"
        )
    array = np.frombuffer(done.stdout[:frames * size], dtype=np.uint8).reshape(frames, height, width, 3)
    return torch.from_numpy(array.astype(np.float32) / 255.0)


def _read_video_audio(path: str, start_frame: int, frames: int, rate: int):
    """Stereo sound of a video file over the same span, at ``rate``; silence when the file has no sound."""
    want = int(round(frames / core.FPS * rate))
    command = [
        _ffmpeg(), "-v", "error", "-ss", f"{start_frame / core.FPS:.6f}", "-t", f"{frames / core.FPS:.6f}",
        "-i", path, "-vn", "-ac", "2", "-ar", str(int(rate)), "-f", "f32le", "-",
    ]
    done = subprocess.run(command, capture_output=True, check=False)
    if done.returncode != 0 or not done.stdout:
        waveform = torch.zeros((1, 2, want))
    else:
        samples = np.frombuffer(done.stdout, dtype=np.float32)
        samples = samples[: len(samples) // 2 * 2].reshape(-1, 2).T.copy()
        waveform = _fit_length(torch.from_numpy(samples)[None], want)
    return {"waveform": waveform, "sample_rate": int(rate)}


def _video_has_sound(path: str) -> bool:
    done = subprocess.run([_ffmpeg(), "-hide_banner", "-i", path], capture_output=True, check=False)
    return b"Audio:" in done.stderr


def _edit_video_file(plan, bank):
    spec = plan["edit_video"]
    item = _bank_entry(bank, spec["ref"])["video"]
    if item is None:
        raise ValueError(
            f"H3 Clip Plan: the plan edits the video '{spec['ref']}'; give it with an 'H3 Plan - Video' node "
            f"named '{spec['ref']}'"
        )
    return item


def _encode_av(vae, audio_vae, images, audio):
    """H3 audio+video latent of a piece of real video, as if H3 had generated it (17k + 5 frames)."""
    frames = int(images.shape[0])
    video = vae.encode(images[:, :, :, :3])
    vae_sr = int(getattr(audio_vae, "audio_sample_rate", 32000))
    waveform = _resample(_stereo(audio["waveform"]), audio["sample_rate"], vae_sr)
    waveform = _fit_length(waveform, int(round(frames / core.FPS * vae_sr)))
    sound = audio_vae.encode(waveform[:1].movedim(1, -1))
    steps = int(round(frames / core.FPS * core.AUDIO_HZ))
    if sound.shape[-1] > steps:
        sound = sound[..., :steps]
    elif sound.shape[-1] < steps:
        sound = torch.nn.functional.pad(sound, (0, steps - sound.shape[-1]))
    return {"samples": [video.to(torch.float32), sound.to(torch.float32)]}


def _start_video_file(plan, bank):
    spec = plan["start_video"]
    item = _bank_entry(bank, spec["ref"])["video"]
    if item is None:
        raise ValueError(
            f"H3 Clip Plan: the plan starts from the video '{spec['ref']}'; give it with an 'H3 Plan - Video' node "
            f"named '{spec['ref']}'"
        )
    return item


START_CONTEXT_SOURCE = 90   # frames of the start video encoded to continue it (17k + 5, on the audio grid)


def _start_video_source(plan, bank, vae, audio_vae, width, height):
    """Latent of the end of the start video segment, for clip 1 to continue it."""
    spec = plan["start_video"]
    item = _start_video_file(plan, bank)
    frames = min(spec["frames"], START_CONTEXT_SOURCE)
    first = spec["end_frame"] - frames
    images = _read_video_frames(item["path"], first, frames, width, height)
    audio = _read_video_audio(item["path"], first, frames, int(getattr(audio_vae, "audio_sample_rate", 32000)))
    return _encode_av(vae, audio_vae, images, audio)


def _shift_song(master_audio, frames: int):
    """The song as heard on the plan timeline: ``frames`` of silence first (the song starts later)."""
    if frames <= 0:
        return master_audio
    rate = int(master_audio["sample_rate"])
    waveform = master_audio["waveform"]
    pad = torch.zeros((*waveform.shape[:-1], int(round(frames / core.FPS * rate))), dtype=waveform.dtype)
    return {"waveform": torch.cat([pad, waveform], dim=-1), "sample_rate": rate}


# --------------------------------------------------------------------------- nodes
class H3PlanReference:
    """One reference image and the name the plan uses for it. Chain several of them."""

    @classmethod
    def INPUT_TYPES(cls):
        images = _input_files(IMAGE_EXTENSIONS) or [""]
        return {
            "required": {
                "name": ("STRING", {"default": "", "tooltip": "Reference name used in the plan, for example 'robot'."}),
                "image": (images, {"image_upload": True, "tooltip": "Pick, upload or drop the image for this reference."}),
            },
            "optional": {
                "references": ("H3_PLAN_REFS", {"tooltip": "Other references to pass along (output of another reference node)."}),
            },
        }

    RETURN_TYPES = ("H3_PLAN_REFS",)
    RETURN_NAMES = ("references",)
    FUNCTION = "add"
    CATEGORY = CATEGORY
    DESCRIPTION = ("Gives a name to a reference image. Connect several of these nodes in a chain and plug the last one "
                   "into 'H3 Plan - Clip': each clip picks the images whose names it lists.")

    @classmethod
    def IS_CHANGED(cls, name, image, references=None):
        try:
            path = folder_paths.get_annotated_filepath(image)
            return f"{name}|{image}|{_mtime(path)}|{os.path.getsize(path)}"
        except Exception:  # noqa: BLE001
            return float("nan")

    @classmethod
    def VALIDATE_INPUTS(cls, name, image):
        if not str(name).strip():
            return "H3 Plan - Reference: give this reference a name (the one used in the plan)"
        if not image or not folder_paths.exists_annotated_filepath(image):
            return f"H3 Plan - Reference '{name}': image not found: {image!r}"
        return True

    def add(self, name, image, references=None):
        picture = _load_image(folder_paths.get_annotated_filepath(image))
        return (_add_to_bank(references, name, "image", picture, "H3 Plan - Reference"),)


class H3PlanVoice:
    """One voice sample and the name the plan uses for the character. Chain it with the reference nodes."""

    @classmethod
    def INPUT_TYPES(cls):
        files = _input_files(AUDIO_EXTENSIONS) or [NO_VOICE]
        return {
            "required": {
                "name": ("STRING", {"default": "", "tooltip": "Same name as the character's image reference, for example 'robot'."}),
                "voice_file": (files, {"tooltip": "Audio file from ComfyUI's input folder: a clean sample of the voice (2 to 15 s). Use the button to pick or drop a file."}),
            },
            "optional": {
                "references": ("H3_PLAN_REFS", {"tooltip": "Other references to pass along (output of another reference or voice node)."}),
            },
        }

    RETURN_TYPES = ("H3_PLAN_REFS",)
    RETURN_NAMES = ("references",)
    FUNCTION = "add"
    CATEGORY = CATEGORY
    DESCRIPTION = ("Gives a voice to a character of the plan, in free mode. Use the same name as the character's "
                   "image (or a name of its own for an off-screen character). Without a voice node, H3 chooses a voice.")

    @classmethod
    def IS_CHANGED(cls, name, voice_file, references=None):
        try:
            path = folder_paths.get_annotated_filepath(voice_file)
            return f"{name}|{voice_file}|{_mtime(path)}|{os.path.getsize(path)}"
        except Exception:  # noqa: BLE001
            return float("nan")

    @classmethod
    def VALIDATE_INPUTS(cls, name, voice_file):
        if not str(name).strip():
            return "H3 Plan - Voice: give this voice a name (the one used in the plan)"
        if not voice_file or voice_file == NO_VOICE or not folder_paths.exists_annotated_filepath(voice_file):
            return (f"H3 Plan - Voice '{name}': audio file not found: {voice_file!r}. Use the 'choose audio file' "
                    "button, or copy the file into ComfyUI/input and press R.")
        return True

    def add(self, name, voice_file, references=None):
        sample = _load_audio(folder_paths.get_annotated_filepath(voice_file))
        return (_add_to_bank(references, name, "voice", sample, "H3 Plan - Voice"),)


class H3PlanVideo:
    """A video file and the name the plan uses for it (start_video). Chain it with the reference nodes."""

    @classmethod
    def INPUT_TYPES(cls):
        files = _input_files(VIDEO_EXTENSIONS) or [NO_VIDEO]
        return {
            "required": {
                "name": ("STRING", {"default": "", "tooltip": "Reference name used in the plan, for example 'intro'."}),
                "video_file": (files, {"tooltip": "Video file from ComfyUI's input folder."}),
            },
            "optional": {
                "references": ("H3_PLAN_REFS", {"tooltip": "Other references to pass along."}),
            },
        }

    RETURN_TYPES = ("H3_PLAN_REFS",)
    RETURN_NAMES = ("references",)
    FUNCTION = "add"
    CATEGORY = CATEGORY
    DESCRIPTION = ("Gives a name to a video, for a plan that continues an existing video ('start_video'). Only the "
                   "segment the plan names is read, at 24 fps, resized to cover the picture size and cropped in the "
                   "middle. Needs ffmpeg (brought by ComfyUI-VideoHelperSuite).")

    @classmethod
    def IS_CHANGED(cls, name, video_file, references=None):
        try:
            path = folder_paths.get_annotated_filepath(video_file)
            return f"{name}|{video_file}|{_mtime(path)}|{os.path.getsize(path)}"
        except Exception:  # noqa: BLE001
            return float("nan")

    @classmethod
    def VALIDATE_INPUTS(cls, name, video_file):
        if not str(name).strip():
            return "H3 Plan - Video: give this video a name (the one used in the plan)"
        if not video_file or video_file == NO_VIDEO or not folder_paths.exists_annotated_filepath(video_file):
            return f"H3 Plan - Video '{name}': video file not found: {video_file!r}"
        return True

    def add(self, name, video_file, references=None):
        path = folder_paths.get_annotated_filepath(video_file)
        item = {"path": path, "digest": _file_digest(path)}
        return (_add_to_bank(references, name, "video", item, "H3 Plan - Video"),)


class H3PlanClip:
    """Prepare one clip of the plan: conditioning and a latent ready to sample."""

    @classmethod
    def INPUT_TYPES(cls):
        return {"required": {
            "clip": ("CLIP",),
            "vae": ("VAE", {"tooltip": "MiniMax H3 video VAE."}),
            "audio_vae": ("VAE", {"tooltip": "MiniMax H3 audio VAE (through H3 Audio VAE Compatibility)."}),
            "plan_file": PLAN_INPUT(),
            "clip_index": ("INT", {"default": 1, "min": 1, "max": core.MAX_CLIPS, "step": 1,
                                   "control_after_generate": True,
                                   "tooltip": "Clip to render. Set the control to 'increment' and queue several runs to render the clips in order."}),
            "seed_mode": (["plan", "random"], {"default": "plan",
                          "tooltip": "plan: the clip's seed from the plan (same inputs = same take). random: a new seed at every run, "
                                     "to make several takes of a clip and choose in 'H3 Plan - Takes'."}),
        }, "optional": {
            "master_audio": ("AUDIO", {"tooltip": "The complete song. Needed in music_video mode, not used in free mode."}),
            "references": ("H3_PLAN_REFS", {"tooltip": "Named images and voices, from the last reference or voice node of the chain."}),
            "width": ("INT", {"forceInput": True, "tooltip": "Picture width, for example from a resolution selector. Overrides the plan."}),
            "height": ("INT", {"forceInput": True, "tooltip": "Picture height. Overrides the plan."}),
            "take_token": ("STRING", {"default": "", "tooltip": "For an external app driving ComfyUI: 8 hexadecimal characters that end the new take's id. Leave empty."}),
            "parent_token": ("STRING", {"default": "", "tooltip": "For an external app driving ComfyUI: continue this take of the previous clip (end of its id) instead of the chosen one. Leave empty."}),
            "seed_override": ("INT", {"default": -1, "min": -1, "max": 2**48, "tooltip": "For an external app driving ComfyUI: explicit seed. -1 = seed_mode decides."}),
        }}

    @classmethod
    def VALIDATE_INPUTS(cls, plan_file):
        # Accept a plan uploaded after the drop-down list was built.
        return True

    RETURN_TYPES = ("CONDITIONING", "LATENT", "AUDIO", "INT", "STRING", "H3_PLAN_CLIP", "STRING")
    RETURN_NAMES = ("positive", "latent", "clip_audio", "seed", "filename_prefix", "plan_clip", "info")
    FUNCTION = "prepare"
    CATEGORY = CATEGORY
    DESCRIPTION = ("Reads one clip from an H3 Clip Plan file and returns everything the sampler needs: "
                   "conditioning built from the clip's prompt, images and voices, and a latent that already "
                   "contains the song slice (music_video mode) and, for a 'continue' clip, the end of the "
                   "previous clip (picture, and in free mode its sound too).")

    @classmethod
    def IS_CHANGED(cls, clip, vae, audio_vae, plan_file, clip_index, seed_mode="plan", master_audio=None,
                   references=None, width=None, height=None, take_token="", parent_token="", seed_override=-1):
        if take_token:
            return f"take:{take_token}"   # an external app asks for a new, named take
        if seed_mode == "random" and int(seed_override) < 0:
            return float("nan")   # a new take at every run
        try:
            path = _plan_path(plan_file)
            plan = core.parse_plan(open(path, "r", encoding="utf-8-sig").read())
            entry, _row = _clip_entry(plan, clip_index)
            stamp = [core.clip_fingerprint(plan, entry["index"]), width, height]
            if entry["continue"] and entry["index"] > 1:
                stamp.append(_resolve(plan)[entry["index"] - 1]["chosen"])
            return json.dumps(stamp)
        except Exception:  # noqa: BLE001 - let prepare() report the real error
            return float("nan")

    def prepare(self, clip, vae, audio_vae, plan_file, clip_index, seed_mode="plan", master_audio=None,
                references=None, width=None, height=None, take_token="", parent_token="", seed_override=-1):
        plan, path = _read_plan(plan_file)
        entry, row = _clip_entry(plan, clip_index)
        index = entry["index"]
        free = plan["mode"] == "free"

        if width is None or height is None:
            width, height = plan["width"], plan["height"]
        if width is None or height is None:
            raise ValueError(
                "H3 Clip Plan: no picture size; connect 'width' and 'height' (for example from a resolution "
                "selector) or set them in the plan"
            )
        problem = core.check_size(int(width), int(height))
        if problem:
            raise ValueError(f"H3 Clip Plan: {problem}; H3 needs sizes that are multiples of 32")

        bank = {name: _bank_entry(references, name) for name in (references or {})}
        missing = [name for name in entry["refs"]
                   if bank.get(name, {}).get("image") is None and bank.get(name, {}).get("voice") is None]
        if missing and "<Picture" in entry["prompt"]:
            details = "; ".join(
                f"'{name}'" + (f" ({plan['refs'][name]})" if plan["refs"].get(name) else "") for name in missing
            )
            raise ValueError(
                f"H3 Clip Plan: clip {index} needs reference(s) that no reference or voice node provides: "
                f"{details}. Names available: {', '.join(sorted(bank)) or 'none'}"
            )
        has_image = {name for name, item in bank.items() if item["image"] is not None}
        has_voice = {name for name, item in bank.items() if item["voice"] is not None}
        edit = plan.get("edit_video") if core.edit_slice(plan, index) else None   # after the segment: no video
        edit_item, edit_sound = None, False
        if edit:
            edit_item = _edit_video_file(plan, bank)
            edit_sound = _video_has_sound(edit_item["path"])    # its sound is <Audio 1>, before the voices
        prompt, image_names, voice_names, notes = core.resolve_prompt(
            entry["prompt"], entry["refs"], has_image, has_voice, use_voices=free, audio_offset=1 if edit_sound else 0,
        )
        if len(image_names) > core.MAX_REFS:
            raise ValueError(f"H3 Clip Plan: clip {index} uses {len(image_names)} images; MiniMax H3 accepts {core.MAX_REFS}")
        if len(voice_names) > core.MAX_VOICES:
            raise ValueError(f"H3 Clip Plan: clip {index} uses {len(voice_names)} voices; MiniMax H3 accepts {core.MAX_VOICES}")
        if len(image_names) + len(voice_names) > core.MAX_REF_FILES:
            raise ValueError(f"H3 Clip Plan: clip {index} uses more than {core.MAX_REF_FILES} reference files")
        voices, voice_notes = _prepare_voices([(name, bank[name]["voice"]) for name in voice_names], audio_vae)
        notes += voice_notes

        if not free:
            if master_audio is None:
                raise ValueError("H3 Clip Plan: this plan is in music_video mode; connect the song to 'master_audio'")
            duration = int(master_audio["waveform"].shape[-1]) / int(master_audio["sample_rate"])
            offset = core.song_offset(plan)
            if row["end_seconds"] - offset / core.FPS > duration + 1.0 / core.FPS:
                raise ValueError(
                    f"H3 Clip Plan: clip {index} ends at {row['end_seconds'] - offset / core.FPS:.3f} s of the song "
                    f"but the song lasts {duration:.3f} s"
                )
            master_audio = _shift_song(master_audio, offset)
        elif master_audio is not None:
            notes.append("master_audio ignored (free mode)")

        ref_video = ref_video_audio = None
        if edit:
            first, count = core.edit_slice(plan, index)
            ref_video = _read_video_frames(edit_item["path"], first, count, int(width), int(height))
            if edit_sound:
                ref_video_audio = _read_video_audio(edit_item["path"], first, count,
                                                    int(getattr(audio_vae, "audio_sample_rate", 32000)))
            notes.append(f"edits {first / core.FPS:.3f}-{(first + count) / core.FPS:.3f} s of the video '{edit['ref']}'"
                         + (" (with its sound)" if edit_sound else " (it has no sound)"))
        positive, latent = _encode(
            clip, vae, audio_vae, prompt, int(width), int(height), entry["frames"],
            [bank[name]["image"] for name in image_names], plan["ref_image_size"], voices,
            ref_video=ref_video, ref_video_audio=ref_video_audio,
        )
        del ref_video

        parent = None
        from_video = index == 1 and entry["continue"] and plan["start_video"] is not None
        if from_video:
            pass   # clip 1 continues the start video, not a take
        elif entry["continue"] and str(parent_token).strip():
            token = str(parent_token).strip()
            states = _resolve(plan)[index - 1]["takes"]
            matches = [tid for tid in states if tid == token or tid.endswith("_" + token)]
            if not matches:
                raise FileNotFoundError(
                    f"H3 Clip Plan: clip {index} should continue take '{token}' of clip {index - 1}, which does not "
                    "exist (its generation may have failed or it was deleted)"
                )
            if states[matches[0]][0] == "stale":
                raise ValueError(f"H3 Clip Plan: take {matches[0]} of clip {index - 1} is stale ({states[matches[0]][1]})")
            parent = matches[0]
        elif entry["continue"]:
            parent = _resolve(plan)[index - 1]["chosen"]
            if not parent:
                raise FileNotFoundError(
                    f"H3 Clip Plan: clip {index} continues clip {index - 1}, which has no usable take; render clip "
                    f"{index - 1} first (or choose one of its takes in 'H3 Plan - Takes')"
                )
        source = _load_take(plan, index - 1, parent) if parent else None
        if from_video:
            source = _start_video_source(plan, bank, vae, audio_vae, int(width), int(height))
        if source is not None:
            video, audio = source["samples"]
            source = {"samples": [video.to(torch.float32), audio.to(torch.float32)]}

        if free:
            if source is not None:
                context_cls = _node_class("MiniMaxH3GeneratedAVMaskedContext", MULTIREF_HINT)
                prepared, used_context = context_cls().prepare(
                    latent=latent,
                    source_latent=source,
                    context_length=row["context"],
                    audio_feather_ticks=plan["audio_feather_ticks"],
                )
            else:
                prepared, used_context = latent, row["context"]
            rate = int(getattr(audio_vae, "audio_sample_rate", 32000))
            samples = int(round(entry["frames"] / core.FPS * rate))
            clip_audio = {"waveform": torch.zeros((1, 2, samples)), "sample_rate": rate}
        else:
            context_cls = _node_class("MiniMaxH3SongMaskedAVContext", MULTIREF_HINT)
            prepared, used_context, clip_audio = context_cls().prepare(
                latent=latent,
                audio_vae=audio_vae,
                master_audio=master_audio,
                clip_start_seconds=row["raw_start_seconds"],
                context_length=row["context"] if source is not None else 0,
                source_fps=24.0,
                crop="disabled",
                source_latent=source,
            )
        if source is not None and int(used_context) != row["context"]:
            raise RuntimeError(
                f"H3 Clip Plan: clip {index} inherited {used_context} frames instead of {row['context']}; "
                "render the previous clip again"
            )

        if int(seed_override) >= 0:
            seed = int(seed_override)
        else:
            seed = entry["seed"] if seed_mode != "random" else random.getrandbits(48)
        token = str(take_token).strip().lower()
        if not (len(token) == 8 and all(c in "0123456789abcdef" for c in token)):
            token = uuid.uuid4().hex[:8]
        take_id = f"s{seed}_{token}"
        prefix = f"{OUTPUT_ROOT}/{plan['project']}/clips/clip_{index:03d}_{entry['name']}_{take_id}"
        timing = (f"song {row['raw_start_seconds']:.3f} -> {row['end_seconds']:.3f} s" if not free
                  else f"timeline {row['raw_start_seconds']:.3f} -> {row['end_seconds']:.3f} s")
        info = (
            f"clip {index}/{len(plan['clips'])} '{entry['name']}' ({plan['mode']}): {entry['frames']} frames, "
            f"{int(width)}x{int(height)}, {timing}, shown from {row['visible_seconds']:.3f} s, "
            f"{('continues the start video' if index == 1 else 'continues clip %d' % (index - 1)) if entry['continue'] else 'new scene'}, "
            f"images: {', '.join(image_names) or 'none'}, voices: {', '.join(voice_names) or 'none'}, "
            f"take {take_id}" + (f" (continues take {parent} of clip {index - 1})" if parent else "")
        )
        if notes:
            info += "\n" + "\n".join("- " + note for note in notes)
        _LOG.info("H3 Clip Plan: %s", info)
        signature = {}
        for name in entry["refs"]:
            item = bank.get(name, {"image": None, "voice": None})
            signature[name] = {
                "image": _digest(item["image"]) if item["image"] is not None else None,
                "voice": _digest(item["voice"]) if (free and item["voice"] is not None) else None,
            }
        if from_video:
            ref = plan["start_video"]["ref"]
            signature.setdefault(ref, {"image": None, "voice": None})["video"] = _start_video_file(plan, bank)["digest"]
        if edit:
            signature.setdefault(edit["ref"], {"image": None, "voice": None})["video"] = edit_item["digest"]
        plan_clip = {"plan_file": str(plan_file), "clip_index": index, "refs": signature, "take_id": take_id,
                     "seed": seed, "width": int(width), "height": int(height), "parent": parent}
        return (positive, prepared, clip_audio, seed, prefix, plan_clip, info)


class H3PlanSaveLatent:
    """Store the sampled latent of a clip so that later runs can continue it and assemble it."""

    @classmethod
    def INPUT_TYPES(cls):
        return {"required": {
            "latent": ("LATENT", {"tooltip": "Output of the sampler."}),
            "plan_clip": ("H3_PLAN_CLIP", {"tooltip": "From 'H3 Plan - Clip': tells which clip of which plan this is."}),
            "delete_stale": ("BOOLEAN", {"default": True, "label_on": "delete stale takes", "label_off": "keep stale takes",
                                         "tooltip": "After saving, delete the takes that are now stale (other images, voices or size, "
                                                    "plan entry changed, or continuing a take that no longer exists), with their "
                                                    "preview videos, and the latents saved by versions older than 3.3. Final videos "
                                                    "are never deleted."}),
        }, "optional": {
            "audio_vae": ("VAE", {"tooltip": "MiniMax H3 audio VAE. Connect it to get the generated sound of the clip on 'audio' (free mode preview)."}),
        }}

    RETURN_TYPES = ("LATENT", "AUDIO", "STRING")
    RETURN_NAMES = ("latent", "audio", "report")
    FUNCTION = "save"
    OUTPUT_NODE = True
    CATEGORY = CATEGORY
    DESCRIPTION = ("Saves the sampled clip as a new take under output/h3_clip_plan/<project>/latents/clip_NNN and "
                   "makes it the chosen take of its clip. "
                   "With the audio VAE connected, 'audio' is the sound H3 generated for the clip.")

    def save(self, latent, plan_clip, delete_stale=True, audio_vae=None):
        plan, _path = _read_plan(plan_clip["plan_file"])
        entry, _row = _clip_entry(plan, plan_clip["clip_index"])
        video, audio = _split_av(latent)
        frames = core.pixel_frames(int(video.shape[2]))
        if frames != entry["frames"]:
            raise ValueError(
                f"H3 Clip Plan: the sampled latent has {frames} frames but clip {entry['index']} is planned with "
                f"{entry['frames']}; the plan file probably changed while the clip was rendering"
            )
        index = entry["index"]
        take_id = plan_clip.get("take_id") or f"s{entry['seed']}_{uuid.uuid4().hex[:8]}"
        path, meta = _take_paths(plan, index, take_id)
        os.makedirs(os.path.dirname(path), exist_ok=True)
        torch.save({
            "video": video.detach().to(device="cpu", dtype=torch.float32).contiguous(),
            "audio": audio.detach().to(device="cpu", dtype=torch.float32).contiguous(),
            "frames": int(frames),
        }, path + ".tmp")
        os.replace(path + ".tmp", path)
        record = {
            "clip": index,
            "name": entry["name"],
            "seed": plan_clip.get("seed", entry["seed"]),
            "saved_at": time.time(),
            "fingerprint": core.take_fingerprint(plan, index),
            "frames": int(frames),
            "width": plan_clip.get("width"),
            "height": plan_clip.get("height"),
            "refs": plan_clip.get("refs") or {},
            "parent": plan_clip.get("parent"),
        }
        with open(meta + ".tmp", "w", encoding="utf-8") as handle:
            json.dump(record, handle, indent=1)
        os.replace(meta + ".tmp", meta)
        # the newest take of a clip becomes its chosen take; the takes node can switch back
        selection = _read_selection(plan)
        selection[index] = take_id
        _write_selection(plan, selection)
        _LOG.info("H3 Clip Plan: saved take %s of clip %d", take_id, index)

        report = [f"clip {index}: take {take_id} saved and chosen"]
        if delete_stale:
            legacy = _delete_legacy_latents(plan)
            if legacy:
                report.append(f"{len(legacy)} latent file(s) from an older version deleted")
        # Automatic deletion only where staleness is certain: the plan entry changed, or the take it
        # continues is gone (repeated, since a deletion can orphan further takes). A take made with
        # other images, voices or size is only reported: an external app may ask the user before resetting.
        # The take just saved is never deleted.
        reported = set()
        while True:
            resolved = _resolve(plan)
            removed = 0
            for number in sorted(resolved):
                kinds = resolved[number].get("kinds", {})
                for tid, (state, why) in resolved[number]["takes"].items():
                    if state != "stale" or tid == take_id:
                        continue
                    if delete_stale and kinds.get(tid) in core.AUTO_DELETE_KINDS:
                        _delete_take(plan, number, tid)
                        report.append(f"clip {number}: stale take {tid} deleted with its previews ({why})")
                        removed += 1
                    elif (number, tid) not in reported:
                        reported.add((number, tid))
                        report.append(f"clip {number}: take {tid} is stale ({why})")
            if not removed:
                break
        if audio_vae is None:
            sound = {"waveform": torch.zeros((1, 2, int(round(frames / core.FPS * 44100)))), "sample_rate": 44100}
        else:
            waveform, rate = _decode_audio(audio_vae, audio.detach())
            sound = {"waveform": _fit_length(waveform, int(round(frames / core.FPS * rate))), "sample_rate": rate}
        return (latent, sound, "\n".join(report))


def _why_unusable(resolved, index):
    takes = resolved.get(index, {}).get("takes", {})
    if not takes:
        return "no take yet"
    states = [state for state, _why in takes.values()]
    if "other" in states:
        return "no take built on the take chosen for the previous clip"
    return "only stale takes (" + "; ".join(sorted({why for _s, why in takes.values()})) + ")"


def _free_soundtrack(plan, count, latents, audio_vae, start_audio=None):
    """Rebuild the sound track of a free-mode plan from the sound generated with each clip.

    The picture assembler keeps, for each clip after the first, only what comes after its
    first ``context`` frames. The sound follows the same rule, with one refinement taken from
    the MultiRef AV assembler: a 'continue' clip regenerated those first frames from a
    protected copy of the previous clip's end (with a short audio feather), so its own version
    of that span is used, which makes the seam continuous. A new scene's first frames are a
    hidden lead-in: there the previous clip's sound is kept, exactly like its picture.
    """
    rows = core.timeline(plan)[:count]
    rate = None
    buffer = None
    for row in rows:
        _video, audio_latent = latents[f"clip_{row['index']}"]["samples"]
        waveform, got = _decode_audio(audio_vae, audio_latent)
        if rate is None:
            rate = got
            buffer = torch.zeros((1, 2, int(round(rows[-1]["end"] / core.FPS * rate))))
        elif got != rate:
            waveform = _resample(waveform, got, rate)
        start = int(round(row["raw_start"] / core.FPS * rate))
        end = int(round(row["end"] / core.FPS * rate))
        waveform = _fit_length(waveform, end - start)
        if row["index"] == 1 and start_audio is not None:
            # the start video's own sound under the segment (read at this rate), then the clips over it
            sound = start_audio(rate)["waveform"]
            span = min(int(sound.shape[-1]), int(buffer.shape[-1]))
            buffer[..., :span] = sound[..., :span]
        if row["context"] and not row["continue"]:
            skip = int(round(row["visible"] / core.FPS * rate)) - start
            buffer[..., start + skip:end] = waveform[..., skip:]
        else:
            buffer[..., start:end] = waveform
        del waveform
    first = int(round(core.final_start(plan) / core.FPS * rate))
    return {"waveform": buffer[..., first:].contiguous(), "sample_rate": rate}


class H3PlanAssemble:
    """Decode every saved clip in order and write the final video with the original song."""

    @classmethod
    def INPUT_TYPES(cls):
        return {
            "required": {
                "video_vae": ("VAE", {"tooltip": "MiniMax H3 video VAE."}),
                "plan_file": PLAN_INPUT(),
                "last_clip": ("INT", {"default": 0, "min": 0, "max": core.MAX_CLIPS, "step": 1,
                                      "tooltip": "Assemble clips 1 to this one. 0 = the whole plan."}),
                "crf": ("INT", {"default": 19, "min": 0, "max": 100, "step": 1}),
            },
            "optional": {
                "master_audio": ("AUDIO", {"tooltip": "music_video mode: the complete song; it becomes the soundtrack untouched."}),
                "audio_vae": ("VAE", {"tooltip": "free mode: MiniMax H3 audio VAE, to decode the sound of every clip. "
                                                 "Also needed to put a start video in the final video."}),
                "references": ("H3_PLAN_REFS", {"tooltip": "Only for a plan with a start video or an edited video: the "
                                                          "'H3 Plan - Video' node (and the others), to put the segment at the start "
                                                          "of the video, or to keep the edited video's original sound."}),
                "width": ("INT", {"forceInput": True, "tooltip": "Picture width of the clips (start video only)."}),
                "height": ("INT", {"forceInput": True, "tooltip": "Picture height of the clips (start video only)."}),
            },
            "hidden": {"prompt": "PROMPT", "extra_pnginfo": "EXTRA_PNGINFO", "unique_id": "UNIQUE_ID"},
        }

    RETURN_TYPES = ("VHS_FILENAMES", "STRING")
    RETURN_NAMES = ("Filenames", "report")
    FUNCTION = "assemble"
    OUTPUT_NODE = True
    CATEGORY = CATEGORY
    DESCRIPTION = ("Builds the final video from the saved clips: overlaps are removed (hard cut) or blended "
                   "according to 'crossfade_frames'. The soundtrack is the original song (music_video mode) or "
                   "the sound generated with each clip, joined at the same places as the picture (free mode). "
                   "It stops before the first clip that is missing or stale, so the beginning of a video can be "
                   "checked before every clip is rendered.")

    @classmethod
    def VALIDATE_INPUTS(cls, plan_file):
        return True

    @classmethod
    def IS_CHANGED(cls, video_vae, plan_file, last_clip, crf, **_other):
        try:
            plan = core.parse_plan(open(_plan_path(plan_file), "r", encoding="utf-8-sig").read())
            resolved = _resolve(plan)
            return json.dumps([resolved[c["index"]]["chosen"] for c in plan["clips"]] + [last_clip, crf])
        except Exception:  # noqa: BLE001
            return float("nan")

    def assemble(self, video_vae, plan_file, last_clip=0, crf=19, master_audio=None, audio_vae=None,
                 references=None, width=None, height=None, prompt=None, extra_pnginfo=None, unique_id=None):
        plan, _path = _read_plan(plan_file)
        wanted = len(plan["clips"]) if int(last_clip) <= 0 else min(len(plan["clips"]), int(last_clip))
        resolved = _resolve(plan)
        count = core.usable_prefix(plan, resolved, wanted)
        if count == 0:
            raise FileNotFoundError(f"H3 Clip Plan: nothing to assemble, clip 1 has {_why_unusable(resolved, 1)}")
        report = [f"assembled clips 1 to {count} of {len(plan['clips'])}, takes: "
                  + ", ".join(f"{i}={resolved[i]['chosen']}" for i in range(1, count + 1))]
        if count < wanted:
            report.append(f"stopped before clip {count + 1}: it has {_why_unusable(resolved, count + 1)}")
        _LOG.info("H3 Clip Plan: %s", "; ".join(report))
        latents = {f"clip_{i}": _load_take(plan, i, resolved[i]["chosen"]) for i in range(1, count + 1)}

        spec = plan["start_video"]
        pieces, start_audio = [], None
        if spec and spec["in_assembly"]:
            pieces, start_audio = self._start_pieces(plan, references, video_vae, audio_vae, latents, width, height)
            report.append(f"starts with {spec['frames'] / core.FPS:.2f} s of the video '{spec['ref']}' "
                          f"({len(pieces)} piece(s))")
        end_frame = core.timeline(plan)[count - 1]["end"]
        first_frame = core.final_start(plan)

        if plan["mode"] == "free":
            if audio_vae is None:
                raise ValueError("H3 Clip Plan: this plan is in free mode; connect the H3 audio VAE to 'audio_vae'")
            audio = _free_soundtrack(plan, count, latents, audio_vae, start_audio)
            edit = plan.get("edit_video")
            edited = core.edit_clip_count(plan) if edit else 0
            if edit and edit["sound"] == "original" and edited:
                # the edited part keeps the sound of the segment, untouched; the clips after it keep their own
                item = _edit_video_file(plan, {name: _bank_entry(references, name) for name in (references or {})})
                rate = int(audio["sample_rate"])
                edit_end = min(core.timeline(plan)[edited - 1]["end"], end_frame)
                if edit_end > first_frame:
                    own = _read_video_audio(item["path"], edit["start_frame"] + first_frame, edit_end - first_frame, rate)
                    waveform = own["waveform"].to(audio["waveform"].dtype)
                    if waveform.shape[1] != audio["waveform"].shape[1]:
                        waveform = waveform.mean(dim=1, keepdim=True).repeat(1, audio["waveform"].shape[1], 1)
                    total = int(audio["waveform"].shape[-1])
                    span = min(total, int(round((edit_end - first_frame) * total / max(1, end_frame - first_frame))))
                    merged = audio["waveform"].clone()
                    merged[..., :span] = _fit_length(waveform, span)
                    audio = {"waveform": merged, "sample_rate": rate}
                    report.append(f"sound: the original sound of the video '{edit['ref']}'"
                                  + (f" until {edit_end / core.FPS:.2f} s, then the generated sound" if edited < count else ""))
        else:
            if master_audio is None:
                raise ValueError("H3 Clip Plan: this plan is in music_video mode; connect the song to 'master_audio'")
            # The song on the plan timeline, cut to the assembled picture (useful for a partial assembly).
            song = _shift_song(master_audio, core.song_offset(plan))
            sample_rate = int(song["sample_rate"])
            first = int(round(first_frame / core.FPS * sample_rate))
            samples = int(round(end_frame / core.FPS * sample_rate))
            waveform = _fit_length(song["waveform"][..., first:samples].clone(), samples - first)
            if start_audio is not None and spec["song"] == "after_video":
                # 'after_video': the segment keeps its own sound, the song starts with clip 1
                own = start_audio(sample_rate)["waveform"].to(waveform.dtype)   # [1, 2, samples]
                if waveform.shape[1] != own.shape[1]:
                    own = own.mean(dim=1, keepdim=True).repeat(1, waveform.shape[1], 1)
                span = min(int(own.shape[-1]), int(waveform.shape[-1]))
                waveform[..., :span] = own[..., :span]
            audio = {"waveform": waveform, "sample_rate": sample_rate}

        if pieces:
            clips = [latents[f"clip_{i}"] for i in range(1, count + 1)]
            latents = {f"clip_{n}": latent for n, latent in enumerate(pieces + clips, start=1)}

        stream_cls = _node_class("MiniMaxH3StreamLiveMusicVideoToVHS", MULTIREF_HINT)
        suffix = "final" if count == len(plan["clips"]) else f"clips_001-{count:03d}"
        result = stream_cls().stream_to_vhs(
            video_vae=video_vae,
            master_audio=audio,
            input_count=len(latents),
            context_frames=plan["context_frames"],
            video_overlap_frames=plan["crossfade_frames"],
            filename_prefix=f"{OUTPUT_ROOT}/{plan['project']}/{plan['project']}_{suffix}",
            pix_fmt="yuv420p",
            crf=int(crf),
            save_metadata=False,
            trim_to_audio=False,
            save_output=True,
            preview_gate=None,
            active_clips=None,
            prompt=prompt,
            extra_pnginfo=extra_pnginfo,
            unique_id=unique_id,
            **latents,
        )
        text = "\n".join(report)
        if isinstance(result, dict):
            values = tuple(result.get("result") or ([],))
            result["result"] = (values[0] if values else [], text)
            result.setdefault("ui", {})["text"] = [text]
            return result
        values = tuple(result) if isinstance(result, (tuple, list)) else (result,)
        return (values[0] if values else [], text)

    @staticmethod
    def _start_pieces(plan, references, video_vae, audio_vae, latents, width, height):
        """Latents of the start video segment, cut into pieces joined like clips, and a reader of its sound."""
        spec = plan["start_video"]
        item = _start_video_file(plan, references)
        if audio_vae is None:
            raise ValueError("H3 Clip Plan: to put the start video in the final video, connect the H3 audio VAE to 'audio_vae'")
        if width is None or height is None:
            video, _audio = latents["clip_1"]["samples"]
            height, width = int(video.shape[-2]) * 16, int(video.shape[-1]) * 16
        vae_sr = int(getattr(audio_vae, "audio_sample_rate", 32000))
        pieces = []
        for first, length in core.segment_chunks(spec["frames"], plan["context_frames"]):
            images = _read_video_frames(item["path"], spec["start_frame"] + first, length, int(width), int(height))
            sound = _read_video_audio(item["path"], spec["start_frame"] + first, length, vae_sr)
            pieces.append(_encode_av(video_vae, audio_vae, images, sound))
            del images

        def start_audio(rate):
            return _read_video_audio(item["path"], spec["start_frame"], spec["frames"], rate)

        return pieces, start_audio


class H3PlanTimeline:
    """Show the timing table of a plan (connect to a Preview Any / Show Text node)."""

    @classmethod
    def INPUT_TYPES(cls):
        return {"required": {"plan_file": PLAN_INPUT()}}

    @classmethod
    def VALIDATE_INPUTS(cls, plan_file):
        return True

    RETURN_TYPES = ("STRING", "INT")
    RETURN_NAMES = ("timeline", "clip_count")
    FUNCTION = "show"
    CATEGORY = CATEGORY

    @classmethod
    def IS_CHANGED(cls, plan_file):
        return float("nan")   # always refresh: it reports the state of the saved clips

    def show(self, plan_file):
        plan, _path = _read_plan(plan_file)
        lines = [core.format_timeline(plan), "", "references to provide (image: 'H3 Plan - Reference', voice: 'H3 Plan - Voice'):"]
        for name in core.reference_names(plan):
            kinds = core.reference_kinds(plan, name)
            lines.append(f"  {name} [{' + '.join(kinds) or 'unused'}]"
                         + (f" - {plan['refs'][name]}" if plan["refs"].get(name) else ""))
        lines.append("")
        resolved = _resolve(plan)
        for clip in plan["clips"]:
            info = resolved[clip["index"]]
            counts = {}
            for state, _why in info["takes"].values():
                counts[state] = counts.get(state, 0) + 1
            detail = ", ".join(f"{n} {state}" for state, n in sorted(counts.items())) or "no take"
            chosen = f"take {info['chosen']}" if info["chosen"] else "NOT USABLE - " + _why_unusable(resolved, clip["index"])
            lines.append(f"clip {clip['index']:>3}: {chosen} ({detail})")
        usable = core.usable_prefix(plan, resolved)
        lines.append(f"assembly would use clips 1 to {usable}" if usable else "assembly: nothing usable yet")
        return ("\n".join(lines), len(plan["clips"]))


# --------------------------------------------------------------------------- takes browser
VIDEO_PREVIEW_EXTENSIONS = (".mp4", ".webm", ".mov", ".mkv", ".gif")


def _takes_listing(plan_file):
    """Everything the takes browser shows, as plain JSON."""
    plan, _path = _read_plan(plan_file)
    resolved = _resolve(plan)
    selection = _read_selection(plan)
    output = folder_paths.get_output_directory()
    clips = []
    for clip in plan["clips"]:
        index = clip["index"]
        records = {record["id"]: record for record in _list_takes(plan, index)}
        takes = []
        for take_id, (state, why) in resolved[index]["takes"].items():
            record = records.get(take_id, {})
            videos = [path for path in _preview_files(plan, index, take_id) if path.lower().endswith(VIDEO_PREVIEW_EXTENSIONS)]
            preview = None
            if videos:
                newest = max(videos, key=_mtime)
                preview = {"filename": os.path.basename(newest), "type": "output",
                           "subfolder": os.path.relpath(os.path.dirname(newest), output).replace(os.sep, "/")}
            takes.append({"id": take_id, "seed": record.get("seed"), "saved_at": record.get("saved_at"),
                          "state": state, "reason": why, "parent": record.get("parent"), "preview": preview,
                          "record": record, "kind": resolved[index].get("kinds", {}).get(take_id)})
        clips.append({"index": index, "name": clip["name"], "continue": clip["continue"], "frames": clip["frames"],
                      "chosen": resolved[index]["chosen"], "selected": selection.get(index), "takes": takes,
                      "why": None if resolved[index]["chosen"] else _why_unusable(resolved, index)})
    return {"project": plan["project"], "mode": plan["mode"], "clips": clips,
            "usable": core.usable_prefix(plan, resolved), "count": len(plan["clips"])}


def _select_take(plan_file, index, take_id):
    """Choose a take; the takes of the previous clips it is built on are chosen with it."""
    plan, _path = _read_plan(plan_file)
    index = int(index)
    takes = _all_takes(plan)
    known = {record["id"]: record for record in takes.get(index, [])}
    if take_id not in known:
        raise ValueError(f"clip {index} has no take {take_id}")
    selection = _read_selection(plan)
    record = known[take_id]
    selection[index] = take_id
    while plan["clips"][index - 1]["continue"] and record.get("parent") and index > 1:
        index -= 1
        selection[index] = record["parent"]
        record = {r["id"]: r for r in takes.get(index, [])}.get(record["parent"], {})
    _write_selection(plan, selection)
    return _takes_listing(plan_file)


def _prune_takes(plan_file):
    """Delete every take that is not the chosen one of its clip, with its previews."""
    plan, _path = _read_plan(plan_file)
    resolved = _resolve(plan)
    removed, freed = 0, 0
    for index, info in resolved.items():
        for take_id in info["takes"]:
            if take_id == info["chosen"]:
                continue
            for path in list(_take_paths(plan, index, take_id)) + _preview_files(plan, index, take_id):
                try:
                    freed += os.path.getsize(path)
                except OSError:
                    pass
            _delete_take(plan, index, take_id)
            removed += 1
    listing = _takes_listing(plan_file)
    listing["pruned"] = {"takes": removed, "megabytes": round(freed / 1e6, 1)}
    return listing


class H3PlanTakes:
    """Browse the takes of every clip, watch their previews and choose the one to assemble."""

    @classmethod
    def INPUT_TYPES(cls):
        return {"required": {"plan_file": PLAN_INPUT()}}

    @classmethod
    def VALIDATE_INPUTS(cls, plan_file):
        return True

    @classmethod
    def IS_CHANGED(cls, plan_file):
        return float("nan")

    RETURN_TYPES = ("STRING",)
    RETURN_NAMES = ("summary",)
    FUNCTION = "summary"
    CATEGORY = CATEGORY
    DESCRIPTION = ("Lists the takes of every clip of the plan. Click a take to watch its preview, then "
                   "'use this take' to choose it. The choice is saved in the project (selection.json) and "
                   "used by the assembly and by the next 'continue' clips. Choosing a take of a 'continue' "
                   "clip also chooses the takes of the previous clips it is built on.")

    def summary(self, plan_file):
        listing = _takes_listing(plan_file)
        lines = [f"project {listing['project']}: clips 1 to {listing['usable']} of {listing['count']} can be assembled"]
        for clip in listing["clips"]:
            lines.append(f"clip {clip['index']:>3} {clip['name']}: " + (f"take {clip['chosen']}" if clip["chosen"] else clip["why"])
                         + f" ({len(clip['takes'])} take(s))")
        return ("\n".join(lines),)


APP_VERSION = "3.11"


def _delete_takes(plan_file, items):
    """Delete the listed takes ({clip, id}) with their previews (asked by an external app, which confirmed first)."""
    plan, _path = _read_plan(plan_file)
    removed = 0
    for item in items or []:
        index, take_id = int(item.get("clip", 0)), str(item.get("id", ""))
        if not (1 <= index <= len(plan["clips"])) or not take_id or "/" in take_id or "\\" in take_id or ".." in take_id:
            continue
        if any(record["id"] == take_id for record in _list_takes(plan, index)):
            _delete_take(plan, index, take_id)
            removed += 1
    listing = _takes_listing(plan_file)
    listing["deleted"] = removed
    return listing


def _save_selection(plan_file, selection):
    """Replace the whole selection of a project (used by an external app before assembling)."""
    plan, _path = _read_plan(plan_file)
    takes = _all_takes(plan)
    clean = {}
    for key, take_id in selection.items():
        index = int(key)
        if take_id and any(record["id"] == take_id for record in takes.get(index, [])):
            clean[index] = take_id
    _write_selection(plan, clean)
    return _takes_listing(plan_file)


def _register_routes():
    try:
        from aiohttp import web
        from server import PromptServer
        routes = PromptServer.instance.routes
    except Exception:  # noqa: BLE001 - not running inside ComfyUI (tests)
        return

    def answer(call):
        try:
            return web.json_response(call())
        except Exception as exc:  # noqa: BLE001
            return web.json_response({"error": str(exc)}, status=400)

    @routes.get("/h3clipplan/takes")
    async def list_takes(request):
        return answer(lambda: _takes_listing(request.query.get("plan", "")))

    @routes.post("/h3clipplan/select")
    async def select_take(request):
        body = await request.json()
        return answer(lambda: _select_take(body.get("plan", ""), body.get("clip"), body.get("take")))

    @routes.post("/h3clipplan/prune")
    async def prune_takes(request):
        body = await request.json()
        return answer(lambda: _prune_takes(body.get("plan", "")))

    @routes.post("/h3clipplan/selection")
    async def save_selection(request):
        body = await request.json()
        return answer(lambda: _save_selection(body.get("plan", ""), body.get("selection") or {}))

    @routes.post("/h3clipplan/delete_takes")
    async def delete_takes(request):
        body = await request.json()
        return answer(lambda: _delete_takes(body.get("plan", ""), body.get("takes")))

    @routes.get("/h3clipplan/ping")
    async def ping(_request):
        return web.json_response({"app": "h3clipplan", "version": APP_VERSION, "plan_version": core.PLAN_VERSION})


_register_routes()


NODE_CLASS_MAPPINGS = {
    "H3PlanReference": H3PlanReference,
    "H3PlanVoice": H3PlanVoice,
    "H3PlanVideo": H3PlanVideo,
    "H3PlanClip": H3PlanClip,
    "H3PlanSaveLatent": H3PlanSaveLatent,
    "H3PlanAssemble": H3PlanAssemble,
    "H3PlanTimeline": H3PlanTimeline,
    "H3PlanTakes": H3PlanTakes,
}
NODE_DISPLAY_NAME_MAPPINGS = {
    "H3PlanReference": "H3 Plan - Reference (named image)",
    "H3PlanVoice": "H3 Plan - Voice (named voice sample)",
    "H3PlanVideo": "H3 Plan - Video (named video, start_video)",
    "H3PlanClip": "H3 Plan - Clip (prompt, refs, song slice)",
    "H3PlanSaveLatent": "H3 Plan - Save Clip Latent",
    "H3PlanAssemble": "H3 Plan - Assemble Final Video",
    "H3PlanTimeline": "H3 Plan - Timeline",
    "H3PlanTakes": "H3 Plan - Takes (choose and preview)",
}
