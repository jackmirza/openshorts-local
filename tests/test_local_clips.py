"""Local gameplay clips: no key needed, bounded ranges, no remote sources."""
import io
import json
import os
from pathlib import Path
import subprocess
import sys

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from pydantic import ValidationError

import local_clips as lc


@pytest.fixture
def workspace(tmp_path, monkeypatch):
    app = FastAPI()
    app.include_router(lc.create_router(tmp_path))
    info = {"duration": 60.0, "width": 1920, "height": 1080, "audio": False}
    monkeypatch.setattr(lc, "probe", lambda path: dict(info))
    return TestClient(app), tmp_path


def upload(client, name="gameplay.mp4"):
    response = client.post('/api/local-clips/sources', files={
        'file': (name, io.BytesIO(b'fixture recording'), 'video/mp4')})
    assert response.status_code == 200, response.text
    return response.json()


def test_local_page_explains_manual_selection_and_has_no_external_scripts(workspace):
    client, _ = workspace
    response = client.get('/local-clips')
    assert response.status_code == 200
    assert 'Moment selection here is manual' in response.text
    assert '<script src=' not in response.text


def test_source_is_kept_locally_with_an_opaque_id(workspace):
    client, root = workspace
    source = upload(client, '../../original.mp4')
    assert source['name'] == 'original.mp4'
    assert (root / 'sources' / source['id'] / 'source.mp4').read_bytes() == b'fixture recording'
    assert client.get(source['video_url']).status_code == 200
    assert not (root / 'original.mp4').exists()


def test_invalid_video_is_rejected_without_leaving_an_upload(workspace, monkeypatch):
    client, root = workspace
    def invalid(_):
        raise lc.HTTPException(400, 'Invalid video')
    monkeypatch.setattr(lc, 'probe', invalid)
    result = client.post('/api/local-clips/sources', files={'file': ('a.mp4', b'bad')})
    assert result.status_code == 400
    assert list((root / 'sources').iterdir()) == []


def test_external_browser_origin_cannot_submit_a_recording(workspace):
    client, root = workspace
    result = client.post('/api/local-clips/sources', files={'file': ('a.mp4', b'bad')},
                         headers={'Origin': 'https://unrelated.example'})
    assert result.status_code == 403
    assert not (root / 'sources').exists()


@pytest.mark.parametrize('start,end', [(10, 5), (0, 181), (-1, 5), (0, float('nan')),
                                     (0, float('inf')), (1, 1)])
def test_invalid_ranges_never_reach_the_renderer(start, end):
    with pytest.raises(ValidationError):
        lc.ClipRange(start=start, end=end)


def test_ranges_past_the_recording_are_rejected(workspace):
    client, root = workspace
    source = upload(client)
    response = client.post('/api/local-clips/jobs', json={
        'source_id': source['id'], 'clips': [{'start': 50, 'end': 70}]})
    assert response.status_code == 400
    assert not (root / 'jobs').exists()


def test_batch_calls_local_renderer_and_exposes_only_finished_files(workspace, monkeypatch):
    client, root = workspace
    source = upload(client)
    seen = []
    def render(src, out, clip, batch):
        seen.append((src, clip.start, clip.end, batch.layout))
        out.write_bytes(b'encoded clip')
        return 'libx264'
    monkeypatch.setattr(lc, 'render_clip', render)
    response = client.post('/api/local-clips/jobs', json={
        'source_id': source['id'], 'layout': 'fit',
        'clips': [{'start': 5, 'end': 25, 'hook': 'A real moment'}]})
    assert response.status_code == 202
    job_id = response.json()['id']
    state = client.get(f'/api/local-clips/jobs/{job_id}').json()
    assert state['status'] == 'completed'
    assert len(seen) == 1 and seen[0][1:] == (5, 25, 'fit')
    assert client.get(state['clips'][0]['url']).content == b'encoded clip'
    assert client.get(f'/api/local-clips/jobs/{job_id}/files/recipe.json').status_code == 404
    assert (root / 'jobs' / job_id / 'recipe.json').exists()


def test_renderer_failure_is_reported_and_does_not_offer_empty_files(workspace, monkeypatch):
    client, _ = workspace
    source = upload(client)
    def fail(*args):
        raise RuntimeError('Encoder unavailable')
    monkeypatch.setattr(lc, 'render_clip', fail)
    job = client.post('/api/local-clips/jobs', json={
        'source_id': source['id'], 'clips': [{'start': 0, 'end': 20}]}).json()
    status = client.get(f'/api/local-clips/jobs/{job["id"]}').json()
    assert status['status'] == 'failed' and status['clips'] == []


def test_restarted_jobs_are_not_left_processing_forever(workspace):
    client, root = workspace
    job_id = 'a' * 32
    folder = root / 'jobs' / job_id
    folder.mkdir(parents=True)
    (folder / 'status.json').write_text(json.dumps({'id': job_id, 'status': 'rendering', 'clips': []}))
    assert client.get(f'/api/local-clips/jobs/{job_id}').json()['status'] == 'interrupted'


def test_hook_text_is_an_image_and_never_part_of_the_filtergraph(tmp_path, monkeypatch):
    seen = []
    def fake_run(cmd, **kwargs):
        seen.append(cmd)
        Path(cmd[-1]).write_bytes(b'output')
        return type('Result', (), {'returncode': 0, 'stderr': ''})()
    monkeypatch.setattr(lc.subprocess, 'run', fake_run)
    monkeypatch.setattr(lc, 'video_encode_args', lambda *_: ['-c:v', 'libx264'])
    batch = lc.ClipBatch(source_id='a' * 32, clips=[
        {'start': 0, 'end': 5, 'hook': "text';movie=/private/file"}])
    lc.render_clip(tmp_path / 'input.mp4', tmp_path / 'out.mp4', batch.clips[0], batch, width=360)
    graph = seen[0][seen[0].index('-filter_complex') + 1]
    assert 'private' not in graph
    assert (tmp_path / 'out.png').is_file()


def test_runtime_files_are_not_committed():
    ignore = (Path(__file__).parents[1] / '.gitignore').read_text()
    assert 'local-data/' in ignore and '.env.*' in ignore


def test_gpu_encoder_failure_falls_back_to_cpu(tmp_path, monkeypatch):
    attempts = []
    def run(cmd, **kwargs):
        attempts.append(cmd)
        failed = 'h264_nvenc' in cmd
        if not failed:
            Path(cmd[-1]).write_bytes(b'cpu encoded clip')
        return type('Result', (), {'returncode': int(failed), 'stderr': 'GPU busy' if failed else ''})()
    monkeypatch.setattr(lc.subprocess, 'run', run)
    monkeypatch.setattr(lc, 'video_encode_args', lambda *_: ['-c:v', 'h264_nvenc'])
    batch = lc.ClipBatch(source_id='a' * 32, clips=[{'start': 0, 'end': 5}])
    encoder = lc.render_clip(tmp_path / 'input.mp4', tmp_path / 'out.mp4', batch.clips[0], batch)
    assert encoder == 'libx264' and len(attempts) == 2
    assert (tmp_path / 'out.mp4').read_bytes() == b'cpu encoded clip'


def test_native_entry_point_can_log_unicode_with_a_legacy_console_encoding():
    env = dict(os.environ, PYTHONIOENCODING='ascii')
    result = subprocess.run([sys.executable, '-c',
                             "import local_clips_app; print('\\u2713 GPU export')"],
                            cwd=Path(__file__).parents[1], env=env, capture_output=True)
    assert result.returncode == 0, result.stderr
    assert '\u2713 GPU export' in result.stdout.decode('utf-8')
