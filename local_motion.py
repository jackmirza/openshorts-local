"""Rank activity in footage with too little speech for the transcript picker."""

import math
import os
import subprocess

import numpy as np

from clip_selection import SHORTS_MIN_SECONDS, clip_count_targets

SAMPLE_FPS = 2
FRAME_WIDTH, FRAME_HEIGHT = 128, 72
PREFERRED_SECONDS = 36


def _sample_motion(path):
    frame_bytes = FRAME_WIDTH * FRAME_HEIGHT
    command = [
        os.environ.get("FFMPEG_BINARY", "ffmpeg"), "-v", "error", "-nostdin",
        "-protocol_whitelist", "file,pipe", "-i", str(path), "-map", "0:V:0",
        "-vf", f"fps={SAMPLE_FPS},scale={FRAME_WIDTH}:{FRAME_HEIGHT},format=gray",
        "-an", "-sn", "-f", "rawvideo", "-threads", "1", "pipe:1",
    ]
    activity = []
    previous = None
    with subprocess.Popen(command, stdout=subprocess.PIPE) as ffmpeg:
        while len(frame := ffmpeg.stdout.read(frame_bytes)) == frame_bytes:
            pixels = np.frombuffer(frame, dtype=np.uint8)
            difference = (0.0 if previous is None else
                          np.abs(np.subtract(pixels, previous, dtype=np.int16)).mean())
            activity.append(difference)
            previous = pixels
        if frame or ffmpeg.wait() or not activity:
            raise RuntimeError("FFmpeg could not sample motion in the recording.")
    return activity


def _rank_activity(activity, duration):
    # Give separate parts of the recording a chance. Search every half-second
    # within each part so the picks can start near its strongest activity.
    frames = min(int(duration * SAMPLE_FPS), len(activity))
    duration = frames / SAMPLE_FPS
    _, limit = clip_count_targets(math.ceil(duration / 90))
    parts = min(limit, int(duration // SHORTS_MIN_SECONDS),
                max(2, int(duration // PREFERRED_SECONDS)))
    if not parts:
        return []

    activity = np.asarray(activity)
    # Limit the influence of a single flash or edit on a whole clip.
    movement = np.r_[0.0, np.cumsum(np.minimum(activity, 30))]
    active = np.r_[0, np.cumsum(activity >= 3)]
    picks = []
    for part in range(parts):
        first = part * frames // parts
        last = (part + 1) * frames // parts
        length = min(PREFERRED_SECONDS * SAMPLE_FPS, last - first)
        if length < SHORTS_MIN_SECONDS * SAMPLE_FPS:
            continue
        starts = np.arange(first, last - length + 1)
        totals = movement[starts + length] - movement[starts]
        active_frames = active[starts + length] - active[starts]
        # Skip static scenes, noise and isolated transitions.
        totals[active_frames < max(4, length // 8)] = 0
        strongest = int(np.argmax(totals))
        if totals[strongest] > 0:
            start = int(starts[strongest]) / SAMPLE_FPS
            end = start + length / SAMPLE_FPS
            minute, second = divmod(int(start), 60)
            picks.append({
                "start": start, "end": end,
                "video_title_for_youtube_short": f"Activity highlight at {minute}:{second:02d}",
                "why": "Sustained visual activity in this part of the recording.",
                "selection_method": "motion",
            })
    return picks


def motion_highlights(path, duration):
    print("Analyzing visual activity locally for Shorts.")
    return _rank_activity(_sample_motion(path), duration)
