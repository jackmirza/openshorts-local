import asyncio
import json
from types import SimpleNamespace

import pytest
from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse

import local_shorts
from clip_selection import shorts_scoring_windows


@pytest.fixture
def inbox(tmp_path):
    for name in ("inbox", "ready", "output"):
        (tmp_path / name).mkdir()
    watcher = local_shorts.ShortsInbox(*(tmp_path / name for name in ("inbox", "ready", "output")),
                                      max_bytes=1000)
    watcher._initialize()
    return watcher


def test_waits_for_completed_stable_download(inbox, monkeypatch):
    now = [0]
    monkeypatch.setattr(local_shorts, "time", SimpleNamespace(monotonic=lambda: now[0]))
    video = inbox.inbox / "match.mp4"
    partly_downloaded = video.with_suffix(".mp4.crdownload")
    partly_downloaded.write_bytes(b"video")
    (inbox.inbox / "match.f136.mp4").write_bytes(b"fragment")
    video.write_bytes(b"first part")
    assert inbox._next_download() is None
    now[0] += 30
    assert inbox._next_download() is None

    partly_downloaded.unlink()
    assert inbox._next_download() is None
    now[0] += 10
    video.write_bytes(b"all the video")
    assert inbox._next_download() is None
    now[0] += local_shorts._STABLE_SECONDS
    _, name, size, _ = inbox._next_download()
    assert name == "match.mp4"
    assert size == len(b"all the video")


def test_reuses_a_submitted_job_after_restart_and_exports_playable_clips(inbox, monkeypatch):
    monkeypatch.setattr(local_shorts, "_STABLE_SECONDS", 0)

    def probe(path, *, include_container_duration=False):
        if path.parent == inbox.inbox:
            return {"duration": 140, "video_start": 0}
        assert include_container_duration
        duration = 20 if path.name == "too-short.mp4" else 40
        width, height = {
            "horizontal.mp4": (1920, 1080),
            "rounded-portrait.mp4": (1216, 2160),
        }.get(path.name, (1080, 1920))
        return {"duration": duration, "container_duration": duration,
                "video_start": 0, "width": width, "height": height}

    monkeypatch.setattr(local_shorts, "probe", probe)
    original = inbox.inbox / "match.mp4"
    original.write_bytes(b"download")
    app = FastAPI()
    jobs = {}
    submissions = []

    @app.post("/api/uploads")
    async def reserve():
        return {"upload_id": "slot"}

    @app.put("/api/uploads/slot")
    async def upload(request: Request):
        assert await request.body() == original.read_bytes()
        return {}

    @app.delete("/api/uploads/slot")
    async def release():
        return {}

    @app.post("/api/process")
    async def submit(request: Request):
        job_id = request.scope[local_shorts.JOB_SCOPE_KEY]
        assert await request.json() == {
            "upload_id": "slot", "acknowledged": True, "output_format": "vertical",
        }
        submissions.append(job_id)
        jobs[job_id] = {"status": "queued"}
        return {"job_id": job_id}

    @app.get("/api/status/{job_id}")
    async def status(job_id: str):
        return jobs.get(job_id, JSONResponse({}, status_code=404))

    async def exercise():
        async with inbox._client(app) as client:
            await inbox._tick(app, client, str)
            (job_id,) = submissions
            # Simulate a stop after the queue accepted the request and before
            # the watcher acknowledged it on disk.
            await inbox._record(job_id, status="waiting")
            restarted = local_shorts.ShortsInbox(inbox.inbox, inbox.ready, inbox.output, 1000)
            restarted._initialize()
            job_folder = inbox.output / job_id
            job_folder.mkdir()
            (job_folder / "captioned.mp4").write_bytes(b"captioned")
            (job_folder / "horizontal.mp4").write_bytes(b"full frame")
            (job_folder / "rounded-portrait.mp4").write_bytes(b"wrong ratio")
            (job_folder / "too-short.mp4").write_bytes(b"short")
            jobs[job_id] = {"status": "completed", "result": {"selected_clips": 4, "clips": [
                {"video_url": f"/videos/{job_id}/captioned.mp4", "start": 10, "end": 50,
                 "horizontal_file": "horizontal.mp4"},
                {"video_url": f"/videos/{job_id}/rounded-portrait.mp4", "start": 52, "end": 92,
                 "horizontal_file": "horizontal.mp4"},
                {"video_url": f"/videos/{job_id}/too-short.mp4", "start": 94, "end": 114},
            ]}}
            await restarted._tick(app, client, str)
            await restarted._tick(app, client, str)
            assert submissions == [job_id]
            record = restarted.jobs[job_id]
            assert (record["status"], record["clips"], record["horizontal_clips"],
                    record["paired_clips"], record["skipped"]) == ("partial", 1, 2, 1, 2)
            assert record["selected_clips"] == 4
            folder = inbox.ready / record["folder"]
            assert (folder / "vertical" / "short-01.mp4").read_bytes() == b"captioned"
            assert (folder / "horizontal" / "short-01.mp4").read_bytes() == b"full frame"
            assert (folder / "horizontal" / "short-02.mp4").read_bytes() == b"full frame"
            assert not (folder / "vertical" / "short-02.mp4").exists()
            clip, horizontal_only = json.loads((folder / "clips.json").read_text())
            assert (clip["start"], clip["end"], clip["duration"]) == (10, 50, 40)
            assert clip["file"] == "vertical/short-01.mp4"
            assert clip["horizontal_file"] == "horizontal/short-01.mp4"
            assert clip["horizontal_duration"] == 40
            assert horizontal_only["horizontal_file"] == "horizontal/short-02.mp4"
            assert "file" not in horizontal_only
            assert original.read_bytes() == b"download"

    asyncio.run(exercise())


def test_motion_finds_distinct_activity_and_skips_static_regions():
    motion = pytest.importorskip("local_motion")
    activity = [0.0] * (120 * motion.SAMPLE_FPS)
    activity[20:60] = [12.0] * 40
    activity[174:220] = [18.0] * 46
    picks = motion._rank_activity(activity, 120)
    assert len(picks) == 2
    assert picks[0]["end"] <= picks[1]["start"]
    assert all(30 <= c["end"] - c["start"] <= 45 for c in picks)
    assert motion._rank_activity([0.0] * len(activity), 120) == []


def test_timed_windows_keep_independent_stories_apart():
    words = [
        {"w": "The first story starts.", "s": 0.5, "e": 3.0},
        {"w": "Its ending.", "s": 3.5, "e": 36.0},
        {"w": "The next story starts.", "s": 39.0, "e": 42.0},
        {"w": "A different ending.", "s": 42.5, "e": 77.0},
    ]
    first, second = shorts_scoring_windows({}, words, 79, 90)
    assert first["whole_section"] and second["whole_section"]
    assert first["end"] <= second["start"]
    assert "[0.50–3.00] The first story starts." in first["timed_text"]
    assert "different ending" not in first["timed_text"]
