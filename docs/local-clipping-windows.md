# Local clipping on Windows

This fork adds a small gameplay workspace to OpenShorts. It uses local FFmpeg
and can use NVIDIA NVENC for encoding. Basic clipping requires no Gemini,
Upload-Post, subscription, Docker container or large language model download.

Moment selection in this workspace is manual. It does not automatically find
the funniest gameplay events or create speech captions. The upstream AI
picker remains a separate option below.

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
encoder failure. Cropping and captions do not establish rights to repost a
creator's recording, the game's assets or its music.

Recordings and finished jobs are kept in `local-data/`; settings and logs are
in `.local-checks/`. These folders, virtual environments and credentials are
excluded from Git and Docker build contexts. Download or back up clips you want
to keep. Sources are capped at 20 GB; this workspace does not automatically
erase your footage. Reopening the page starts a fresh editing session; saved
files remain on disk.

## Original AI picker (optional)

The full OpenShorts application needs Docker Desktop with the WSL2 engine and
the larger processing dependencies. This fork includes `compose.local.yml` and
`scripts/Start-Local.ps1` for that stack. Stop the native clipping worker first,
because both frontends use port 5175.

```powershell
.\scripts\Stop-Clipping.ps1
.\scripts\Start-Local.ps1 -Build -InstallModel
```

This is an additional, multi-gigabyte installation. It is not required for the
working timestamp clipper. The profile limits jobs and clip workers to one,
uses local Whisper and Ollama, releases the LLM after each request, and does
not supply cloud-service credentials. Exact capacity still depends on the
recording and available RAM/VRAM.

The transcript picker works from spoken content. Mostly silent gameplay
instead uses the upstream Gemini vision path, which needs a key and has quota
limits; a text-only Ollama model cannot replace it. Use the timestamp workspace
for silent gameplay when you do not want cloud analysis. Publishing and paid
AI actor generation are not configured by this setup.

## Verify exports

The smoke test creates its own moving test pattern; it downloads no game footage:

```powershell
.\.venv-local\Scripts\python.exe scripts\smoke_local_clips.py
```

If FFmpeg is not on PATH, supply `--ffmpeg` and `--ffprobe` with their executable
paths. Results are written to `.local-checks/export-validation.json`. The test
checks dimensions, duration, audio/mute behavior and complete decoding of both
framing modes.
