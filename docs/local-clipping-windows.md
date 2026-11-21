# Local clipping on Windows

This fork adds a small gameplay workspace to OpenShorts. It uses local FFmpeg
and can use NVIDIA NVENC for encoding. Basic clipping requires no Gemini,
Upload-Post, subscription, Docker container or large language model download.

Moment selection in this workspace is manual. The Docker setup below adds
automatic highlight selection and speech captions.

## Start

Prerequisites: Windows 10/11, Python 3.11 or newer, and FFmpeg with ffprobe.
FFmpeg can be on PATH or in a sibling folder named `ffmpeg`.

Run this once from the repository folder:

```powershell
.\scripts\Setup-Clipping.ps1
```

Then double-click **Start Clipping.cmd**, or run:

```powershell
.\scripts\Start-Clipping.ps1
```

Open **http://localhost:5175/local-clips**. The hidden background worker stays
running when the setup terminal closes. It binds only to the local machine.
Double-click **Stop Clipping.cmd** when finished; recordings and exports are
kept. These launchers do not change PowerShell's execution policy.
If you use the PowerShell launcher's `-Port` option, stop the existing worker
first. Only one worker per checkout can use the local recording store.

## Make clips

1. Choose a recording you own or a creator-provided file you are permitted to edit.
2. Click **Open recording**. The maximum source is 2 GB and eight hours.
3. Preview the source and mark start/end, or enter seconds or `mm:ss`.
4. Add up to 15 clip ranges, each between one and 180 seconds. Hook text is optional.
5. Choose **Full gameplay + blurred background** to preserve the complete picture
   and HUD, or choose a fixed vertical crop and its horizontal focus.
6. Click **Create clips**, review the output and download the MP4s.

Output is 1080×1920 at 30 fps. Source audio is kept unless **Mute source audio**
is selected. NVENC is used when available, with an x264 fallback after a GPU
encoder failure. Clip ranges must start and end within the video track.
For browser playback, H.264 MP4 with AAC audio is the most compatible choice.
If your browser cannot preview a recording, find timestamps in a local media
player and enter them in the clipper. Cropping and captions do not establish
rights to repost a creator's recording, the game's assets or its music.

Recordings and finished jobs are kept in `local-data/`; settings and logs are
in `.local-checks/`. These folders, virtual environments and credentials are
excluded from Git and Docker build contexts. Download or back up clips you want
to keep. Sources are capped at 20 GB; this workspace does not automatically
erase your footage. After you click **Create clips**, the page URL identifies
that export. Reload or bookmark it to recover the downloads and clip ranges.
If the worker stopped during an export, reopen the same link to see any
finished clips and run the saved ranges again.

## Automatic Shorts inbox

The full OpenShorts application needs Docker Desktop with the WSL2 engine and
the larger processing dependencies. This fork includes `compose.local.yml` and
`scripts/Start-Local.ps1` for that stack. Stop the native clipping worker first,
because both frontends use port 5175.

```powershell
.\scripts\Stop-Clipping.ps1
.\scripts\Start-Local.ps1 -Build -InstallModel
```

This is an additional, multi-gigabyte installation. It is not required for the
working timestamp clipper. The launcher checks the configured Ollama model
before building the backend. Use `-InstallModel` for the first install or
when switching models. The profile limits jobs and clip workers to one,
uses local Whisper and Ollama, releases the LLM after each request, and does
not supply cloud-service credentials. Exact capacity still depends on the
recording and available RAM/VRAM.

The file-only profile uses FFmpeg for reframing and can still use NVIDIA
encoding. Its build skips the optional Rust renderer and the YouTube
JavaScript runtimes. It downloads the locked Python packages in parallel,
resumes incomplete transfers where supported, and caches completed wheels
across builds.

1. Set your download destination to `shorts-inbox/` or put a completed video
   there. Use recordings you own or have permission from the creator to reuse.
   The inbox accepts MP4, MKV, WebM, MOV and other common video files up to 2 GB.
2. Keep Docker Desktop and the local stack running. The watcher waits for a
   file to stabilize, then queues it once and finishes one inbox job at a time.
3. Open `shorts-ready/` to review both versions of each 30–45 second highlight.
   The source folder has `vertical/` MP4s for YouTube Shorts and Facebook Reels
   at 1080×1920 and `horizontal/` MP4s for standard YouTube uploads and landscape
   Facebook videos. The horizontal export fits the full source frame inside 1920×1080.
   Matching filenames identify the same moment in each orientation.
   `clips.json` lists both files, original time ranges, suggested titles and
   selection reasons. See `jobs.json` for progress, the count in each orientation
   and the number of complete pairs. A `partial` result includes the playable
   exports. The original stays in the inbox.

Whisper aligns clip boundaries and captions to the speech. Ollama scores
spoken sections for their opening hook, standalone context and payoff. With
little or no speech, the picker uses frame-to-frame activity to propose
gameplay highlights.
The number of clips depends on how many distinct moments fit the duration
band. Review the picks and publish them from your own YouTube account.

## Verify exports

The smoke test creates its own moving test pattern; it downloads no game footage:

```powershell
.\.venv-local\Scripts\python.exe scripts\smoke_local_clips.py
```

If FFmpeg is not on PATH, supply `--ffmpeg` and `--ffprobe` with their executable
paths. Results are written to `.local-checks/export-validation.json`. The test
checks dimensions, duration, audio/mute behavior and complete decoding of both
framing modes.
