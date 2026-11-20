"""Local gameplay clips: no key needed, bounded ranges, no remote sources."""
import asyncio
import io
import json
import os
from pathlib import Path
import subprocess
import sys
from types import SimpleNamespace

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from pydantic import ValidationError

import local_clips as lc


@pytest.fixture
def workspace(tmp_path, monkeypatch):
    app = FastAPI()
    lc.install_clipper(app, tmp_path)
    info = {"duration": 60.0, "video_start": 0.0, "width": 1920, "height": 1080,
            "audio": False, "media_type": "video/mp4"}
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


@pytest.mark.parametrize('declare_length', [False, True])
def test_upload_body_is_capped_before_a_recording_is_saved(tmp_path, monkeypatch, declare_length):
    monkeypatch.setattr(lc, 'MAX_UPLOAD_BYTES', 16)
    monkeypatch.setattr(lc, '_MULTIPART_OVERHEAD', 192)
    app = FastAPI()
    lc.install_clipper(app, tmp_path)
    read_count = 0
    body = (b'--clip\r\nContent-Disposition: form-data; name="file"; filename="a.mp4"\r\n'
            b'Content-Type: video/mp4\r\n\r\n' + b'x' * 256 + b'\r\n--clip--\r\n')

    async def receive():
        nonlocal read_count
        read_count += 1
        assert read_count == 1
        return {'type': 'http.request', 'body': body, 'more_body': False}

    headers = [(b'content-type', b'multipart/form-data; boundary=clip')]
    if declare_length:
        headers.append((b'content-length', str(len(body)).encode()))
    response = []

    async def send(message):
        response.append(message)

    path = '/api/local-clips/sources'
    scope = {'type': 'http', 'asgi': {'version': '3.0'}, 'http_version': '1.1', 'scheme': 'http',
             'method': 'POST', 'path': path, 'root_path': '', 'query_string': b'', 'headers': headers}
    asyncio.run(app(scope, receive, send))
    assert response[0]['status'] == 413
    assert read_count == (0 if declare_length else 1)
    assert not (tmp_path / 'sources').exists()


@pytest.mark.parametrize('start,end', [(10, 5), (0, 181), (-1, 5), (0, float('nan')),
                                     (0, float('inf')), (1, 1)])
def test_invalid_ranges_never_reach_the_renderer(start, end):
    with pytest.raises(ValidationError):
        lc.ClipRange(start=start, end=end)


def test_exactly_one_second_at_fractional_timestamps_is_allowed():
    assert lc.ClipRange(start=1.01, end=2.01).duration == 1


@pytest.mark.parametrize('video_fields', [
    {'duration': '2.0', 'start_time': '0.0'},
    {'tags': {'DURATION': '00:00:02.000000000'}},
])
def test_probe_uses_video_end_when_audio_continues(tmp_path, monkeypatch, video_fields):
    formats = 'matroska,webm' if 'tags' in video_fields else 'mov,mp4,m4a,3gp,3g2,mj2'
    meta = {'format': {'format_name': formats, 'duration': '6'},
            'streams': [
                {'codec_type': 'video', 'width': 320, 'height': 180, **video_fields},
                {'codec_type': 'audio', 'duration': '6'},
            ]}
    monkeypatch.setattr(lc.subprocess, 'run', lambda *_args, **_kwargs:
                        SimpleNamespace(stdout=json.dumps(meta)))
    info = lc.probe(tmp_path / 'source.mp4')
    assert info['duration'] == 2
    assert info['audio'] is True


def test_cover_art_does_not_count_as_a_video_track(tmp_path, monkeypatch):
    meta = {'format': {'format_name': 'mov,mp4', 'duration': '6'},
            'streams': [{'codec_type': 'video', 'width': 64, 'height': 64,
                         'disposition': {'attached_pic': 1}}, {'codec_type': 'audio'}]}
    monkeypatch.setattr(lc.subprocess, 'run', lambda *_args, **_kwargs:
                        SimpleNamespace(stdout=json.dumps(meta)))
    with pytest.raises(lc.HTTPException, match='playable video track'):
        lc.probe(tmp_path / 'source.mp4')


def test_ranges_past_the_recording_are_rejected(workspace):
    client, root = workspace
    source = upload(client)
    response = client.post('/api/local-clips/jobs', json={
        'source_id': source['id'], 'clips': [{'start': 50, 'end': 70}]})
    assert response.status_code == 400
    assert not (root / 'jobs').exists()


def test_old_upload_is_probed_again_before_clipping(workspace, monkeypatch):
    client, root = workspace
    source = upload(client)
    metadata = root / 'sources' / source['id'] / 'source.json'
    info = json.loads(metadata.read_text())
    del info['video_start']
    metadata.write_text(json.dumps(info))
    monkeypatch.setattr(lc, 'probe', lambda _path: {'duration': 2.0, 'video_start': 0.0})
    response = client.post('/api/local-clips/jobs', json={
        'source_id': source['id'], 'clips': [{'start': 3, 'end': 5}]})
    assert response.status_code == 400
    assert not (root / 'jobs').exists()


def test_ranges_before_the_video_track_starts_are_rejected(workspace, monkeypatch):
    client, root = workspace
    monkeypatch.setattr(lc, 'probe', lambda _path: {
        'duration': 5, 'video_start': 2, 'media_type': 'video/mp4'})
    source = upload(client)
    response = client.post('/api/local-clips/jobs', json={
        'source_id': source['id'], 'clips': [{'start': 0, 'end': 1.5}]})
    assert response.status_code == 400
    assert 'before the source video track' in response.json()['detail']
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


def test_saved_export_restores_source_and_ranges_after_restart(workspace, monkeypatch):
    client, root = workspace
    source = upload(client)

    def render(_source, output, _clip, _batch):
        output.write_bytes(b'completed clip')
        return 'libx264'

    monkeypatch.setattr(lc, 'render_clip', render)
    recipe = {'source_id': source['id'], 'layout': 'crop', 'focus': 24, 'mute': True,
              'clips': [{'start': 1.01, 'end': 4.01, 'hook': 'Saved hook'}]}
    job = client.post('/api/local-clips/jobs', json=recipe).json()
    app = FastAPI()
    lc.install_clipper(app, root)
    restarted = TestClient(app)
    url = f'/api/local-clips/jobs/{job["id"]}?restore=true'
    restored = restarted.get(url).json()
    assert restored['recipe'] == recipe
    assert restored['source']['video_url'] == source['video_url']
    assert restored['status'] == 'completed'
    (root / 'sources' / source['id'] / 'source.mp4').unlink()
    restored = restarted.get(url).json()
    assert restored['source'] is None
    assert restarted.get(restored['clips'][0]['url']).content == b'completed clip'


@pytest.mark.parametrize('finished', [0, 1])
def test_renderer_failure_keeps_only_the_finished_clips(workspace, monkeypatch, finished):
    client, root = workspace
    source = upload(client)

    def fail(_source, out, clip, _batch):
        if clip.start < finished:
            out.write_bytes(b'completed clip')
            return 'libx264'
        out.write_bytes(b'incomplete clip')
        out.with_suffix('.png').write_bytes(b'temporary overlay')
        raise RuntimeError('Encoder unavailable')
    monkeypatch.setattr(lc, 'render_clip', fail)
    job = client.post('/api/local-clips/jobs', json={
        'source_id': source['id'], 'clips': [{'start': i, 'end': i + 2} for i in range(finished + 1)]}).json()
    status = client.get(f'/api/local-clips/jobs/{job["id"]}').json()
    assert status['status'] == 'failed' and len(status['clips']) == finished
    if finished:
        assert client.get(status['clips'][0]['url']).content == b'completed clip'
    folder = root / 'jobs' / job['id']
    assert len(list(folder.glob('*.mp4'))) == finished
    assert list(folder.glob('*.png')) == []
    assert client.get(f'/api/local-clips/jobs/{job["id"]}/files/clip_{finished + 1:02d}.mp4').status_code == 404


def test_restarted_jobs_are_not_left_processing_forever(workspace):
    client, root = workspace
    job_id = 'a' * 32
    folder = root / 'jobs' / job_id
    folder.mkdir(parents=True)
    (folder / 'clip_01.mp4').write_bytes(b'finished')
    (folder / '.clip_02.mp4').write_bytes(b'unfinished')
    (folder / '.clip_02.png').write_bytes(b'overlay')
    (folder / 'status.json').write_text(json.dumps({'id': job_id, 'status': 'rendering',
                                                    'clips': [{'name': 'clip_01.mp4'}]}))
    assert client.get(f'/api/local-clips/jobs/{job_id}').json()['status'] == 'interrupted'
    assert list(folder.glob('*.mp4')) == [folder / 'clip_01.mp4']
    assert list(folder.glob('*.png')) == []
    assert client.get(f'/api/local-clips/jobs/{job_id}/files/clip_01.mp4').content == b'finished'


def test_hook_text_is_an_image_and_never_part_of_the_filtergraph(tmp_path, monkeypatch):
    seen = []
    def fake_run(cmd, **kwargs):
        seen.append(cmd)
        Path(cmd[-1]).write_bytes(b'output')
        return type('Result', (), {'returncode': 0, 'stderr': ''})()
    monkeypatch.setattr(lc.subprocess, 'run', fake_run)
    monkeypatch.setattr(lc, 'video_encode_args', lambda *_: ['-c:v', 'libx264'])
    monkeypatch.setattr(lc, 'probe', lambda _path: {'duration': 5})
    batch = lc.ClipBatch(source_id='a' * 32, clips=[
        {'start': 0, 'end': 5, 'hook': "text';movie=/private/file"}])
    lc.render_clip(tmp_path / 'input.mp4', tmp_path / 'out.mp4', batch.clips[0], batch, width=360)
    graph = seen[0][seen[0].index('-filter_complex') + 1]
    assert 'private' not in graph
    assert (tmp_path / 'out.png').is_file()


def test_successful_ffmpeg_exit_does_not_accept_audio_only_output(tmp_path, monkeypatch):
    def run(cmd, **kwargs):
        if '-show_streams' in cmd:
            return SimpleNamespace(stdout=json.dumps({'format': {'duration': '2'},
                                                     'streams': [{'codec_type': 'audio'}]}))
        Path(cmd[-1]).write_bytes(b'audio-only MP4')
        return SimpleNamespace(returncode=0, stderr='')

    monkeypatch.setattr(lc.subprocess, 'run', run)
    monkeypatch.setattr(lc, 'video_encode_args', lambda *_: ['-c:v', 'libx264'])
    batch = lc.ClipBatch(source_id='a' * 32, clips=[{'start': 3, 'end': 5}])
    output = tmp_path / 'out.mp4'
    with pytest.raises(RuntimeError, match='contains no playable video'):
        lc.render_clip(tmp_path / 'source.mp4', output, batch.clips[0], batch)
    assert not output.exists()


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
    monkeypatch.setattr(lc, 'probe', lambda _path: {'duration': 5})
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
