"""Small native Windows entry point for the local gameplay workspace.

No Torch, Docker, Google key or Ollama download is needed for timestamp cuts.
The original OpenShorts application remains available through compose.local.yml.
"""
import os
import sys

from fastapi import FastAPI
from fastapi.responses import RedirectResponse

from local_clips import create_router

# The upstream encoder announces NVENC with Unicode. Redirected Windows
# consoles default to a legacy code page; logging must not fail a video job.
for _stream in (sys.stdout, sys.stderr):
    if hasattr(_stream, "reconfigure"):
        _stream.reconfigure(encoding="utf-8", errors="replace")

app = FastAPI(title="OpenShorts Local Gameplay", docs_url=None, redoc_url=None)
app.include_router(create_router(os.environ.get("LOCAL_CLIP_DIR", "local-data")))


@app.get("/")
def index():
    return RedirectResponse("/local-clips")


@app.get("/health/ready")
def health():
    return {"status": "ready", "application": "openshorts-local", "mode": "timestamp"}


@app.get("/api/config")
def config():
    return {"billingEnabled": False, "localClipper": True, "timestampOnly": True}
