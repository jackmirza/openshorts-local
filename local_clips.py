"""Opt-in, self-hosted timestamp clipping for gameplay without an AI API.

The upstream transcript picker is useful for spoken recordings. This path
also handles silent footage: the editor chooses moments and FFmpeg renders
them locally. It neither downloads sources nor publishes the result.
"""
from __future__ import annotations

import json
import math
import os
from pathlib import Path
import re
import shutil
import subprocess
import threading
import uuid
from typing import Literal
from urllib.parse import urlsplit

from fastapi import APIRouter, BackgroundTasks, Depends, File, HTTPException, Request, UploadFile
from fastapi.responses import FileResponse
from pydantic import BaseModel, ConfigDict, Field, model_validator

from ffmpeg_utils import QUALITY_FAST, video_encode_args
from jsonfile import write_json_atomic

_ID = re.compile(r"^[0-9a-f]{32}$")
_render_lock = threading.Lock()
_active_jobs: set[str] = set()
MAX_UPLOAD_BYTES = 2 * 1024**3
MAX_STORE_BYTES = 20 * 1024**3


def _local_origin(request: Request):
    origin = request.headers.get("origin")
    if origin:
        parsed = urlsplit(origin)
        if parsed.scheme not in ("http", "https") or parsed.hostname not in (
                "localhost", "127.0.0.1", "::1"):
            raise HTTPException(403, "Open the local clipper directly on localhost.")


def _item(root: Path, kind: str, item_id: str) -> Path:
    if not _ID.fullmatch(item_id):
        raise HTTPException(404, "Not found")
    parent = (root / kind).resolve()
    if parent.parent != root:
        raise HTTPException(404, "Not found")
    result = (parent / item_id).resolve()
    if result.parent != parent:
        raise HTTPException(404, "Not found")
    return result


def probe(path: Path) -> dict:
    try:
        result = subprocess.run(
            [os.environ.get("FFPROBE_BINARY", "ffprobe"), "-v", "error", "-protocol_whitelist", "file,pipe",
             "-show_format", "-show_streams", "-of", "json", str(path)],
            capture_output=True, text=True, timeout=30, check=True)
        data = json.loads(result.stdout)
        stream = next(s for s in data["streams"] if s["codec_type"] == "video")
        duration = float(data.get("format", {}).get("duration") or stream.get("duration"))
        if not math.isfinite(duration) or duration <= 0 or duration > 8 * 3600:
            raise ValueError("unsupported duration")
        return {"duration": duration, "width": int(stream["width"]),
                "height": int(stream["height"]),
                "audio": any(s.get("codec_type") == "audio" for s in data["streams"])}
    except (subprocess.SubprocessError, ValueError, KeyError, StopIteration, TypeError) as exc:
        raise HTTPException(400, "Use a readable video no longer than eight hours.") from exc


class ClipRange(BaseModel):
    model_config = ConfigDict(extra="forbid", allow_inf_nan=False)
    start: float = Field(ge=0)
    end: float = Field(gt=0)
    hook: str = Field(default="", max_length=100)

    @model_validator(mode="after")
    def duration_bounds(self):
        if not 1 <= self.end - self.start <= 180:
            raise ValueError("Each clip must be between 1 and 180 seconds.")
        return self


class ClipBatch(BaseModel):
    model_config = ConfigDict(extra="forbid", allow_inf_nan=False)
    source_id: str = Field(pattern=r"^[0-9a-f]{32}$")
    clips: list[ClipRange] = Field(min_length=1, max_length=15)
    layout: Literal["fit", "crop"] = "fit"
    focus: float = Field(default=50, ge=0, le=100)
    mute: bool = False


def filtergraph(layout: str, focus: float, width: int = 1080, hook: bool = False) -> str:
    height = width * 16 // 9
    if layout == "fit":
        # Keep the complete gameplay picture and HUD; only the filler is blurred.
        graph = (f"[0:v]split=2[b][f];[b]scale=270:480:force_original_aspect_ratio=increase,"
                 f"crop=270:480,boxblur=12:2,scale={width}:{height}[bg];"
                 f"[f]scale={width}:{height}:force_original_aspect_ratio=decrease[fg];"
                 "[bg][fg]overlay=(W-w)/2:(H-h)/2,setsar=1[base]")
    else:
        graph = (f"[0:v]scale={width}:{height}:force_original_aspect_ratio=increase,"
                 f"crop={width}:{height}:(iw-ow)*{focus / 100:.6f}:(ih-oh)/2,"
                 "setsar=1[base]")
    return graph + (";[base][1:v]overlay=0:0[out]" if hook else ";[base]null[out]")


def _hook_image(text: str, path: Path, width: int):
    # User text is pixels in a PNG, never pasted into a shell or FFmpeg filter.
    from PIL import Image, ImageDraw, ImageFont
    image = Image.new("RGBA", (width, width * 16 // 9), (0, 0, 0, 0))
    draw = ImageDraw.Draw(image)
    font_path = Path(__file__).parent / "fonts" / "Montserrat-ExtraBold.ttf"
    candidates = [font_path, Path("/usr/share/fonts/truetype/liberation2/LiberationSans-Bold.ttf")]
    font = next((ImageFont.truetype(str(p), max(16, width // 23))
                 for p in candidates if p.exists()), ImageFont.load_default())
    words, lines, line = text.split(), [], ""
    for word in words:
        proposed = (line + " " + word).strip()
        if line and draw.textlength(proposed, font=font) > width * .84:
            lines.append(line)
            line = word
        else:
            line = proposed
    if line:
        lines.append(line)
    text = "\n".join(lines)
    box = draw.multiline_textbbox((0, 0), text, font=font, spacing=8)
    text_width, text_height = box[2] - box[0], box[3] - box[1]
    x, y = (width - text_width) / 2, width * .23
    draw.rounded_rectangle((width * .05, y - 20, width * .95, y + text_height + 28),
                           radius=12, fill=(0, 0, 0, 210))
    draw.multiline_text((x, y - box[1]), text, font=font, spacing=8,
                        align="center", fill="white")
    image.save(path)


def render_clip(source: Path, output: Path, clip: ClipRange, batch: ClipBatch,
                width: int = 1080):
    hook = clip.hook.strip()
    overlay = output.with_suffix(".png")
    if hook:
        _hook_image(hook, overlay, width)
    base = [os.environ.get("FFMPEG_BINARY", "ffmpeg"), "-hide_banner", "-loglevel", "error", "-y", "-nostdin",
            "-ss", str(clip.start), "-protocol_whitelist", "file,pipe", "-i", str(source)]
    if hook:
        base += ["-loop", "1", "-i", str(overlay)]
    base += ["-t", str(clip.end - clip.start), "-filter_complex_threads", "1",
             "-filter_complex", filtergraph(batch.layout, batch.focus, width, bool(hook)),
             "-map", "[out]"]
    base += ["-an"] if batch.mute else ["-map", "0:a?", "-c:a", "aac", "-b:a", "160k"]
    encoding = video_encode_args(QUALITY_FAST)
    tail = ["-r", "30", "-pix_fmt", "yuv420p", "-movflags", "+faststart", str(output)]
    result = subprocess.run(base + encoding + tail, capture_output=True, text=True, timeout=900)
    if result.returncode and "h264_nvenc" in encoding:
        encoding = ["-c:v", "libx264", "-preset", "fast", "-crf", "18", "-threads", "2"]
        result = subprocess.run(base + encoding + tail, capture_output=True, text=True, timeout=900)
    if result.returncode:
        raise RuntimeError("FFmpeg could not render this clip: " + result.stderr[-1000:])
    if not output.exists() or output.stat().st_size == 0:
        raise RuntimeError("FFmpeg returned an empty clip.")
    return "h264_nvenc" if "h264_nvenc" in encoding else "libx264"


def create_router(data_root: str | Path) -> APIRouter:
    root = Path(data_root).resolve()
    router = APIRouter(dependencies=[Depends(_local_origin)])

    def source_info(source_id):
        folder = _item(root, "sources", source_id)
        try:
            return folder, json.loads((folder / "source.json").read_text(encoding="utf-8"))
        except (OSError, ValueError) as exc:
            raise HTTPException(404, "Source not found. Upload the recording first.") from exc

    @router.get("/local-clips", response_class=FileResponse)
    def page():
        return FileResponse(Path(__file__).with_name("local-clips.html"), media_type="text/html")

    @router.post("/api/local-clips/sources")
    def upload(file: UploadFile = File(...)):
        if file.size is not None and file.size > MAX_UPLOAD_BYTES:
            raise HTTPException(413, "Recordings must be smaller than 2 GB.")
        existing = sum(p.stat().st_size for p in (root / "sources").glob("*/source.mp4"))
        if existing + (file.size or MAX_UPLOAD_BYTES) > MAX_STORE_BYTES:
            raise HTTPException(413, "Local sources have reached the 20 GB limit. Back up and remove old sources.")
        source_id = uuid.uuid4().hex
        folder = _item(root, "sources", source_id)
        folder.mkdir(parents=True)
        path = folder / "source.mp4"
        try:
            size = 0
            with path.open("wb") as out:
                while chunk := file.file.read(1024 * 1024):
                    size += len(chunk)
                    if size > MAX_UPLOAD_BYTES:
                        raise HTTPException(413, "Recordings must be smaller than 2 GB.")
                    out.write(chunk)
            info = probe(path)
            info.update(id=source_id, name=(file.filename or "recording").replace("\\", "/").split("/")[-1][:120])
            write_json_atomic(str(folder / "source.json"), info)
            return {**info, "video_url": f"/api/local-clips/sources/{source_id}/video"}
        except Exception:
            shutil.rmtree(folder)
            raise

    @router.get("/api/local-clips/sources/{source_id}/video")
    def source_video(source_id: str):
        folder, _ = source_info(source_id)
        return FileResponse(folder / "source.mp4")

    def run_batch(job_id: str, batch: ClipBatch):
        folder = _item(root, "jobs", job_id)
        status_path = folder / "status.json"
        state = {"id": job_id, "status": "queued", "clips": []}
        _active_jobs.add(job_id)
        try:
            with _render_lock:
                state["status"] = "rendering"
                write_json_atomic(str(status_path), state)
                source_folder, _ = source_info(batch.source_id)
                for index, clip in enumerate(batch.clips, 1):
                    name = f"clip_{index:02d}.mp4"
                    encoder = render_clip(source_folder / "source.mp4", folder / name, clip, batch)
                    state["clips"].append({"name": name, "hook": clip.hook, "encoder": encoder,
                                           "duration": clip.end - clip.start,
                                           "url": f"/api/local-clips/jobs/{job_id}/files/{name}"})
                    write_json_atomic(str(status_path), state)
                state["status"] = "completed"
        except Exception as exc:
            state.update(status="failed", error=str(exc)[-1200:])
        finally:
            write_json_atomic(str(status_path), state)
            _active_jobs.discard(job_id)

    @router.post("/api/local-clips/jobs", status_code=202)
    def submit(batch: ClipBatch, background: BackgroundTasks):
        _, info = source_info(batch.source_id)
        if any(c.end > info["duration"] + .01 for c in batch.clips):
            raise HTTPException(400, "A clip ends after the source recording.")
        job_id = uuid.uuid4().hex
        folder = _item(root, "jobs", job_id)
        folder.mkdir(parents=True)
        write_json_atomic(str(folder / "recipe.json"), batch.model_dump())
        write_json_atomic(str(folder / "status.json"), {"id": job_id, "status": "queued", "clips": []})
        _active_jobs.add(job_id)
        background.add_task(run_batch, job_id, batch)
        return {"id": job_id, "status": "queued"}

    @router.get("/api/local-clips/jobs/{job_id}")
    def status(job_id: str):
        path = _item(root, "jobs", job_id) / "status.json"
        try:
            state = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, ValueError) as exc:
            raise HTTPException(404, "Job not found") from exc
        if state["status"] in ("queued", "rendering") and job_id not in _active_jobs:
            state.update(status="interrupted", error="The server restarted. Submit the clip ranges again.")
        return state

    @router.get("/api/local-clips/jobs/{job_id}/files/{name}")
    def download(job_id: str, name: str):
        if not re.fullmatch(r"clip_\d{2}\.mp4", name):
            raise HTTPException(404, "Clip not found")
        folder = _item(root, "jobs", job_id)
        state = status(job_id)
        if name not in {c["name"] for c in state["clips"]} or not (folder / name).is_file():
            raise HTTPException(404, "Clip not found")
        return FileResponse(folder / name, filename=name, media_type="video/mp4")

    return router
