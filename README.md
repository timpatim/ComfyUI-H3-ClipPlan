# ComfyUI-H3-ClipPlan (v3.6)

*Version française : [README.fr.md](README.fr.md).*

> **Disclaimer: this project is entirely vibe-coded.** All of its code, documentation, workflows
> and examples were written by an AI assistant (Claude, by Anthropic), following the author's
> requests. The author specified the features and tested the result in use, but neither wrote
> nor reviewed any of the code. Read it, test it on your own setup and use it at your own
> risk; issues and corrections are welcome.

ComfyUI nodes that generate a MiniMax H3 video from a **JSON plan file**, one clip per run,
with a graph of about thirty nodes instead of two hundred.

Two modes, chosen in the plan for the whole project:

- **`music_video`**: you provide the song; it is injected into every clip and stays untouched.
  H3 only generates the picture, in sync with the song.
- **`free`**: no song; H3 generates picture and sound together (dialogue, ambience), with each
  character's voice if you provide a sample of it.

For each clip, the plan gives its length, whether it continues the previous shot, the references
it uses and its prompt. It holds nothing machine-specific: reference images and resolution are
chosen in the workflow.

The plan format is specified in [`docs/PLAN_SPEC.md`](docs/PLAN_SPEC.md) (JSON schema:
`docs/plan.schema.json`). `docs/LLM_SYSTEM_PROMPT.md` is a system prompt to have an LLM write a
plan for you.

**Status.** The plan logic (reading, validation, timing, placeholders) is covered by tests.
Clip-by-clip generation has been run in ComfyUI with MiniMax H3. Some later features were
first checked in simulation (nodes called with dummy models).

## Quick start with the examples

The `examples/` folder holds two small, ready-to-run projects, with their pictures (drawn by
code) and song (synthesized), distributed under the same license as the plugin:

| Plan | Mode | Needs | Length |
|---|---|---|---|
| `robot_beach.plan.json` | `music_video`, 3 clips | `robot.png`, `beach.png`, `robot_beach_song.mp3` | 19.3 s |
| `robot_workshop.plan.json` | `free`, 2 clips with dialogue | `robot.png`, `workshop.png` (a voice sample named `robot` is optional) | 11.5 s |

1. Copy every file of `examples/` into `ComfyUI/input/`.
2. Open `workflows/H3 Clip Plan - 1 Generate.json` (or `… Generate (free).json`): plan, images
   and song are already selected. Check the model files, set the run count to the number of
   clips (3 or 2) and run.
3. Open `workflows/H3 Clip Plan - 2 Assemble.json` (or `… Assemble (free).json`) and run it to
   get the final video.

## Version 3.6: integrated app removed

- The plan editor and generation app that versions 3.4 and 3.5 served at `/h3clipplan/app` is
  removed, with its server routes (`/h3clipplan/settings`, `/h3clipplan/digest`). The nodes,
  the « H3 Plan - Takes » node and the take routes used by external tools (`takes`, `select`,
  `prune`, `selection`, `delete_takes`, `ping`) are unchanged.
- The optional inputs `take_token`, `parent_token` and `seed_override` of « H3 Plan - Clip »
  stay available to any external app that drives ComfyUI through its API.
- The interface of the takes node and the workflows are in English; the free-mode workflows are
  renamed `… (free).json`.
- New examples: `robot_beach` (music video) and `robot_workshop` (free mode), with their pictures
  and song.

## Version 3.3: takes

Each generation of a clip is kept as a **take**: its latent, its signature and its preview.

```text
output/h3_clip_plan/<project>/latents/clip_003/s<seed>_<id>.pt     latent of the take
output/h3_clip_plan/<project>/latents/clip_003/s<seed>_<id>.json   signature
output/h3_clip_plan/<project>/clips/clip_003_<name>_s<seed>_<id>_00001.mp4   preview
output/h3_clip_plan/<project>/selection.json                       chosen take for each clip
```

- **Several takes**: on « H3 Plan - Clip », `seed_mode` set to `random` gives a new seed at each
  run. Running the same clip three times (`clip_index` control on `fixed`) gives three takes to
  compare. `plan` uses the seed from the plan.
- **The most recent take becomes the chosen take** of its clip.
- **Choosing**: the « H3 Plan - Takes » node (assembly workflows) lists the takes of each clip.
  A click plays the preview, « use this take » chooses it. The choice is
  saved in the project (`selection.json`), not in the workflow.
- **Continuations**: a take of a `continue` clip is built on one specific take of the previous
  clip. It is only usable if that take is chosen (otherwise it is shown dotted). Choosing such a
  take also chooses the one of the previous clip. A `continue` clip generated next starts from
  the chosen take of the previous clip.
- **Stale takes**: plan changed (seed aside), other image or voice than the most recent take,
  other resolution, or the take of the previous clip is gone. With `delete_stale` (on by
  default) they are deleted with their previews after each generation. Latents from earlier
  versions (without takes) are deleted too.
- **Assembly** uses the chosen take of each clip and stops before the first clip with no usable
  take. Its report lists the takes used.
- **Cleanup**: « delete unchosen takes » in the takes node
  removes every take that is not chosen, with its preview. Final videos are never deleted.
- A take weighs about 15 MB (10 s clip at 960 × 544).

Note: changing the image or voice of a reference makes every take that uses it stale, even if
you only redo one clip. For an isolated test, turn `delete_stale` off.

## Version 3.1

- Fix for the « H3 Plan - Voice » node: its file field stayed stuck (« UNKNOWN »). It is now a
  plain list of the audio files in `ComfyUI/input` (subfolders included), with a
  `choose audio file` button and drag and drop of a file onto the node. The uploaded file is
  copied to `ComfyUI/input/voices/`.
- VOICE nodes of a 3.0 workflow must be deleted and recreated (or reopen the
  « 1 Generate (free) » workflow).

## What's new in version 3

- **`free` mode**: H3 generates the sound of each clip. Continuations take over the end of the
  previous clip in picture **and sound** (1.6 s copied and protected, released smoothly over
  0.2 s), through the `MiniMaxH3GeneratedAVMaskedContext` node of the MultiRef pack.
- **« H3 Plan - Voice » node**: a voice sample (2 to 15 s) with the same name as the character's
  image, or a name of its own for an off-screen character. Without a voice node, H3 picks the
  voice.
- **Placeholders in prompts**: `{picture:name}` and `{voice:name}`. The node numbers them
  (`<Picture N>`, `<Audio N>`) according to what the workflow actually provides; a
  `{voice:name}` line with no voice provided is removed. Prompts using `<Picture N>` from older
  plans still work.
- **`free` mode assembly**: the sound of each clip is decoded and joined at the same points as
  the picture.
- **`dialogue` field** per clip: the checker controls speakers, presence of the text in the
  prompt and speech rate (at most 3 words per second).
- Two more workflows: « 1 Generate (free) » and « 2 Assemble (free) ».
- v1 and v2 plans, v2 workflows and clips already generated remain usable.

## What's new in version 2

- **Resolution in the workflow**: `width` and `height` inputs on the clip node, to connect to a
  resolution selector.
- **Plan chosen from a list**, with a browse button and drag and drop of a `.json` file onto the
  node. The file is copied to `ComfyUI/input/plans/`.
- **References by label**: one « H3 Plan - Reference » node per image, with a name and an image
  dropped on it. No more imposed folder or file names.
- Version 1 plans still load; their reference folder is ignored.
- Clips already generated remain usable.

## Contents

| File | Role |
|---|---|
| `nodes.py` | The seven ComfyUI nodes and the server routes for choosing takes |
| `core.py` | Plan reading, validation and timing (pure Python) |
| `web/h3_plan_upload.js` | Button and drag and drop to pick a plan or a voice |
| `web/h3_plan_takes.js` | UI of the take chooser node (list, video player) |
| `plan_tool.py` | Command-line checker |
| `docs/PLAN_SPEC.md` | Plan format specification |
| `docs/plan.schema.json` | JSON schema of the plan |
| `docs/LLM_SYSTEM_PROMPT.md` | System prompt to have an LLM write a plan |
| `examples/` | Two example projects: plans, pictures and song (see Quick start) |
| `workflows/` | Generation then assembly, for each of the two modes |
| `tests/test_core.py`, `tests/fixtures/` | Tests of the plan logic and their test plans |

## Requirements

- ComfyUI with native MiniMax H3 support (`MiniMaxH3ReferenceToVideo` node).
- The `ComfyUI-H3-Motion-Context-MultiRef` pack: it provides the song injection, the picture and
  sound continuation of `free` mode and the final assembly, which these nodes call.
- `ComfyUI-VideoHelperSuite`, to write the videos.

No extra Python dependency.

## Installation or update

With git (recommended):

```bash
cd ComfyUI/custom_nodes
git clone https://github.com/timpatim/ComfyUI-H3-ClipPlan.git
# to update later:
cd ComfyUI-H3-ClipPlan
git pull
```

Without git: download the repository archive (« Code » button → « Download ZIP ») and extract it
to `ComfyUI/custom_nodes/ComfyUI-H3-ClipPlan` (the folder must have this exact name, without the
`-main` suffix).

Then:

1. Restart ComfyUI and reload the browser page.
2. Open the workflows from the `workflows/` folder: those of version 1 lack the new inputs.

Nothing to install with pip. Tests of the plan logic, without ComfyUI:
`python tests/test_core.py`.

## Using `free` mode

1. Open `workflows/H3 Clip Plan - 1 Generate (free).json` and check the models. There is no
   song.
2. Pick the plan on the « H3 Plan - Clip » node (list, button or drag and drop).
3. Images: one « REF … » node per image. Voices: one « VOICE … » node per character, with
   the same name as its image. All nodes are chained; the last one goes into `references`.
4. Set the resolution, `clip_index` to 1 on `increment`, the run count to the number of clips,
   and launch. The preview of each clip includes the generated sound.
5. Open `workflows/H3 Clip Plan - 2 Assemble (free).json`, pick the same plan and launch. Any
   background music is added later, in editing.

For the example plan `robot_workshop.plan.json` you need `robot` (image, voice optional) and
`workshop` (image).

## Using `music_video` mode

1. Open `workflows/H3 Clip Plan - 1 Generate.json`, check the models, load the song.
2. **Plan**: on the « H3 Plan - Clip » node, click `choose plan file (.json)` or drop the file on
   the node. It then appears in the `plan_file` list.
3. **References**: in each « REF … » node, drop the image you want. The `name` field must be the
   name used in the plan. The « H3 Plan - Timeline » node of the second workflow shows the list
   of expected names with their description.
4. **Resolution**: set it in the resolution selector connected to the clip node.
5. Set `clip_index` to 1, leave its control on `increment`, set the run count to the number of
   clips in the plan, and launch.
6. Open `workflows/H3 Clip Plan - 2 Assemble.json`, load the same song, pick the same plan and
   launch: the final video is written with the original song.

Everything is saved under `ComfyUI/output/h3_clip_plan/<project>/`:

```text
latents/clip_001/<take>.pt          takes of each clip (see "takes")
clips/clip_001_<name>_<take>_*.mp4  preview of each take, overlap included
selection.json                      chosen take for each clip
<project>_final_*.mp4               final video
```

### Adding or removing a reference

Duplicate a « REF … » node, change its name and image, then insert it in the chain: its
`references` output goes to the `references` input of the next node, and the last node of the
chain goes to the clip node. The order of the chain does not matter: the plan says which
references each clip uses and in which order.

### Redoing a clip

Put its number in `clip_index`, set the control to `fixed`, run once. For another take with the
same prompt, change its `seed` in the plan.

A clip marked `"continue": true` starts from the end of the chosen take of the previous clip. If
a clip is redone, the new take becomes the chosen one, and the takes of the following `continue`
clips are no longer usable (they are still built on the old one): redo them up to the next
new-scene clip, or go back to the old take in the takes node.

### Changing the resolution

All clips of a project must have the same size. After a resolution change, regenerate all clips,
or change the plan's `project` name to start from an empty folder.

### Checking a plan without ComfyUI

```text
python plan_tool.py check examples/robot_beach.plan.json --song-duration 20
python plan_tool.py grid 22.5 40.1        possible cut points around these times
python plan_tool.py frames                allowed clip lengths
```

## The nodes

| Node | Inputs | Outputs |
|---|---|---|
| **H3 Plan - Reference** | `name`, `image` (list, upload, drag and drop), optional `references` | `references` |
| **H3 Plan - Voice** | `name`, `voice_file` (list, `choose audio file` button, drag and drop), optional `references` | `references` |
| **H3 Plan - Clip** | `clip`, `vae`, `audio_vae`, `plan_file`, `clip_index`, `seed_mode`, and optionally `master_audio` (required in `music_video`), `references`, `width`, `height` | `positive`, `latent`, `clip_audio` (silence in `free`), `seed`, `filename_prefix`, `plan_clip`, `info` |
| **H3 Plan - Save Clip Latent** | `latent` (sampler output), `plan_clip`, `delete_stale`, optional `audio_vae` | `latent`, `audio` (generated sound, if `audio_vae` is connected), `report` |
| **H3 Plan - Assemble Final Video** | `video_vae`, `plan_file`, `last_clip`, `crf`, and `master_audio` (`music_video`) or `audio_vae` (`free`) | video files, `report` |
| **H3 Plan - Takes** | `plan_file` | list of takes, video preview, choice and cleanup (in the node) |
| **H3 Plan - Timeline** | `plan_file` | timing table, expected reference names, clip status |

### Wiring of the generation workflow

If the provided workflow does not load, the graph can be rebuilt by hand as follows:

| From | To |
|---|---|
| Load CLIP | H3 Plan - Clip `clip` |
| Load VAE (video) | H3 Plan - Clip `vae`, VAE Decode `vae` |
| Load VAE (audio) → H3 Audio VAE Compatibility | H3 Plan - Clip `audio_vae` |
| Load Audio (`music_video` only) | H3 Plan - Clip `master_audio` |
| Resolution selector `width`, `height` | H3 Plan - Clip `width`, `height` |
| Chain of H3 Plan - Reference (last node) | H3 Plan - Clip `references` |
| H3 Plan - Clip `positive` | BasicGuider `conditioning` |
| H3 Plan - Clip `latent` | SamplerCustomAdvanced `latent_image` |
| H3 Plan - Clip `seed` | RandomNoise `noise_seed` |
| SamplerCustomAdvanced `output` | H3 Plan - Save Clip Latent `latent` |
| H3 Plan - Clip `plan_clip` | H3 Plan - Save Clip Latent `plan_clip` |
| H3 Plan - Save Clip Latent `latent` | VAE Decode `samples` |
| VAE Decode | Video Combine `images` |
| H3 Plan - Clip `clip_audio` (`music_video`) or H3 Plan - Save Clip Latent `audio` (`free`) | Video Combine `audio` |
| H3 Plan - Clip `filename_prefix` | Video Combine `filename_prefix` |

The model chain (Load Diffusion Model → attention → LoRA → Sigma Shift → BasicGuider and
BasicScheduler) and the sampler choice stay as in the original workflow.

## Known limitations

- The nodes rely on the internal behaviour of three existing nodes. An update of ComfyUI or of
  the MultiRef pack may require an adaptation.
- If uploading a plan with the button fails, copy the file to `ComfyUI/input/plans/` and press R
  to refresh the list.
- 64 clips at most, a limit of the assembly node.
- `crossfade_frames` applies to every join: hard cuts everywhere or crossfades everywhere.
- The first clip of the plan always starts at the beginning of the song.
- `free` mode: a new scene after a cut also loses its first 39 frames (hidden lead-in), as in
  `music_video` mode. It costs extra computation but keeps a single assembler for both modes.
- `free` mode: the stability of a voice over a long chain of continuations has not been measured
  yet. A voice generated without a sample may change from one clip to the next.
- MiniMax H3 accepts at most 3 voices per clip, 15 s in total: with several voices, each sample
  is cut to its first seconds (7.5 s for two voices, 5 s for three).

## License

GPL-3.0 (see `LICENSE`), like the `ComfyUI-H3-Motion-Context-MultiRef` pack these nodes depend on.
