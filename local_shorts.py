"""Process completed recordings from an opt-in, local Shorts inbox."""

import asyncio
import json
import logging
import os
from pathlib import Path
import re
import shutil
import stat
import time
from urllib.parse import unquote, urlsplit
import uuid

import httpx
from fastapi import HTTPException

from clip_selection import (SHORTS_HORIZONTAL_SIZE, SHORTS_MAX_SECONDS,
                            SHORTS_MIN_SECONDS, SHORTS_VERTICAL_SIZE)
from jsonfile import write_json_atomic
from local_clips import probe

JOB_SCOPE_KEY = "openshorts.local_inbox_job"
_VIDEO_EXTENSIONS = {".mp4", ".mkv", ".webm", ".mov", ".m4v", ".avi", ".ts", ".mpeg", ".mpg"}
_PART_SUFFIXES = (".part", ".crdownload", ".download", ".tmp")
_ACTIVE = {"waiting", "queued", "processing"}
_POLL_SECONDS = 5
_STABLE_SECONDS = 15
log = logging.getLogger(__name__)


def _file_version(path):
    try:
        details = path.lstat()
        if stat.S_ISREG(details.st_mode) and details.st_size:
            return details.st_size, details.st_mtime_ns
    except FileNotFoundError:
        pass
    return None


def _job_id(name, version):
    return str(uuid.uuid5(uuid.NAMESPACE_URL, json.dumps(["openshorts-inbox", name, *version])))


def _copy_render(source, destination, aspect):
    try:
        metadata = probe(source, include_container_duration=True)
    except HTTPException as exc:
        if exc.status_code != 400:
            raise
        return None
    length = metadata["container_duration"]
    video_length = metadata["duration"] - metadata["video_start"]
    width, height = aspect
    if not (SHORTS_MIN_SECONDS <= min(length, video_length)
            and max(length, video_length) <= SHORTS_MAX_SECONDS
            and height * metadata["width"] == width * metadata["height"]):
        return None
    destination.parent.mkdir(parents=True, exist_ok=True)
    scratch = destination.with_suffix(".tmp")
    shutil.copyfile(source, scratch)
    os.replace(scratch, destination)
    return round(length, 3)


class SourceChanged(Exception):
    """A download changed while it was being accepted."""


class ShortsInbox:
    def __init__(self, inbox, ready, output, max_bytes):
        self.inbox = Path(inbox)
        self.ready = Path(ready)
        self.output = Path(output)
        self.max_bytes = max_bytes
        self.ledger = self.ready / "jobs.json"
        self.observed = {}
        self.jobs = {}

    def _initialize(self):
        if not self.inbox.is_dir():
            raise OSError(f"Shorts inbox directory is missing: {self.inbox}")
        self.ready.mkdir(parents=True, exist_ok=True)
        if self.ledger.exists():
            self.jobs = json.loads(self.ledger.read_text(encoding="utf-8"))

    async def _record(self, job_id, **changes):
        updated = {**self.jobs, job_id: {**self.jobs.get(job_id, {}), **changes}}
        await asyncio.to_thread(write_json_atomic, self.ledger, updated, indent=2)
        self.jobs = updated

    def _next_download(self):
        now = time.monotonic()
        seen = {}
        eligible = []
        for path in self.inbox.iterdir():
            if (path.name.startswith(".") or path.suffix.lower() not in _VIDEO_EXTENSIONS
                    or path.stem.lower().endswith((*_PART_SUFFIXES, ".ytdl"))
                    or re.search(r"\.f\d+$", path.stem)
                    or any(path.with_name(path.name + ext).exists() for ext in _PART_SUFFIXES)):
                continue
            version = _file_version(path)
            if not version:
                continue
            job_id = _job_id(path.name, version)
            if job_id in self.jobs:
                continue
            seen[job_id] = self.observed.get(job_id, now)
            if now - seen[job_id] >= _STABLE_SECONDS:
                eligible.append((version[1], path.name, version[0], job_id))
        self.observed = seen
        return min(eligible, default=None)

    async def _upload(self, app, client, job_id, entry):
        path = self.inbox / entry["source"]
        expected = entry["bytes"], entry["modified_ns"]
        if _file_version(path) != expected:
            raise SourceChanged
        if expected[0] > self.max_bytes:
            raise ValueError(f"Source exceeds {self.max_bytes // 1024**2} MB.")
        metadata = await asyncio.to_thread(probe, path)
        if metadata["duration"] - metadata["video_start"] < SHORTS_MIN_SECONDS:
            raise ValueError(f"Source is shorter than {SHORTS_MIN_SECONDS:g} seconds.")

        slot = (await client.post("/api/uploads", json={"filename": path.name})).raise_for_status().json()
        upload_id = slot["upload_id"]
        url = f"/api/uploads/{upload_id}"

        async def chunks():
            with path.open("rb") as source:
                details = os.fstat(source.fileno())
                if (details.st_size, details.st_mtime_ns) != expected:
                    raise SourceChanged
                while chunk := await asyncio.to_thread(source.read, 1024**2):
                    yield chunk

        async def submit(scope, receive, send):
            scope[JOB_SCOPE_KEY] = job_id
            await app(scope, receive, send)

        try:
            response = await client.put(url, content=chunks())
            response.raise_for_status()
            if _file_version(path) != expected:
                raise SourceChanged
            async with self._client(submit) as trusted_client:
                response = await trusted_client.post("/api/process", json={
                    "upload_id": upload_id, "acknowledged": True, "output_format": "vertical",
                })
                response.raise_for_status()
        finally:
            await client.delete(url)

    def _export(self, job_id, entry, clips):
        stem = re.sub(r"[^a-zA-Z0-9_-]+", "-", Path(entry["source"]).stem).strip("-_")
        folder = f"{stem[:60] or 'recording'}-{job_id[:12]}"
        export_dir = self.ready / folder
        job_dir = self.output / job_id
        records = []
        for index, clip in enumerate(clips, 1):
            filename = f"short-{index:02d}.mp4"
            record = {
                "start": clip["start"], "end": clip["end"],
                "title": clip.get("video_title_for_youtube_short"),
                "hook": clip.get("viral_hook_text"),
                "reason": clip.get("why"),
                "score": clip.get("predicted_score"),
                "method": clip.get("selection_method", "transcript"),
            }
            # Copy the exact local file served by status (captioned if present).
            parts = unquote(urlsplit(clip.get("video_url") or "").path).split("/")
            if (len(parts) == 4 and parts[:3] == ["", "videos", job_id]
                    and "\\" not in parts[-1] and parts[-1].endswith(".mp4")):
                vertical = f"vertical/{filename}"
                duration = _copy_render(job_dir / parts[-1], export_dir / vertical,
                                        SHORTS_VERTICAL_SIZE)
                if duration is not None:
                    record.update(file=vertical, duration=duration)
            wide_source = clip.get("horizontal_file")
            if (isinstance(wide_source, str) and wide_source.endswith(".mp4")
                    and "/" not in wide_source and "\\" not in wide_source):
                horizontal = f"horizontal/{filename}"
                wide_seconds = _copy_render(job_dir / wide_source, export_dir / horizontal,
                                            SHORTS_HORIZONTAL_SIZE)
                if wide_seconds is not None:
                    record.update(horizontal_file=horizontal, horizontal_duration=wide_seconds)
            if "file" in record or "horizontal_file" in record:
                records.append(record)
        if not records:
            raise ValueError("No playable clips between 30 and 45 seconds were rendered.")
        write_json_atomic(export_dir / "clips.json", records, indent=2)
        paired = sum("file" in clip and "horizontal_file" in clip for clip in records)
        return {
            "folder": folder, "status": "completed" if paired == len(clips) else "partial",
            "clips": sum("file" in clip for clip in records),
            "horizontal_clips": sum("horizontal_file" in clip for clip in records),
            "paired_clips": paired, "skipped": len(clips) - len(records),
        }

    async def _tick(self, app, client, job_error):
        active = next((key for key, job in self.jobs.items() if job["status"] in _ACTIVE), None)
        if active is None:
            candidate = await asyncio.to_thread(self._next_download)
            if candidate is None:
                return
            modified, name, size, active = candidate
            await self._record(active, source=name, bytes=size, modified_ns=modified, status="waiting")
        entry = self.jobs[active]

        try:
            response = await client.get(f"/api/status/{active}")
            if response.status_code == 404:
                await self._upload(app, client, active, entry)
                await self._record(active, status="queued")
                log.info("Shorts inbox queued %s as %s", entry["source"], active)
                return

            job = response.raise_for_status().json()
            if job["status"] == "completed":
                exported = await asyncio.to_thread(self._export, active, entry, job["result"]["clips"])
                await self._record(active, **exported)
            elif job["status"] == "failed":
                await self._record(active, status="failed", error=job_error(job["logs"])[-500:])
            elif job["status"] != entry["status"]:
                await self._record(active, status=job["status"])
        except SourceChanged:
            await self._record(active, status="superseded", error="The download changed or was moved.")
        except FileNotFoundError:
            await self._record(active, status="failed", error="A source or finished clip file is missing.")
        except (ValueError, HTTPException) as exc:
            await self._record(active, status="failed", error=str(getattr(exc, "detail", str(exc))))
        except httpx.HTTPStatusError as exc:
            if not 400 <= exc.response.status_code < 500 or exc.response.status_code == 429:
                raise
            await self._record(active, status="failed", error=exc.response.text[:500])

    @staticmethod
    def _client(app):
        return httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://localhost")

    async def run(self, app, is_active, job_error):
        initialized = False
        async with self._client(app) as client:
            while True:
                if is_active():
                    try:
                        if not initialized:
                            await asyncio.to_thread(self._initialize)
                            initialized = True
                            log.info("Watching %s for Shorts downloads", self.inbox)
                        await self._tick(app, client, job_error)
                    except Exception:
                        log.exception("Shorts inbox check failed")
                await asyncio.sleep(_POLL_SECONDS)
