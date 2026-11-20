"""Exercise a running local clipper with a generated moving test pattern.

No game footage is fetched. Results and source files stay in .local-checks/.
"""
from __future__ import annotations

import argparse
import json
import os
from pathlib import Path
import subprocess
import time
import urllib.request
import uuid


def request(base, path, data=None, content_type=None):
    headers = {"Content-Type": content_type} if content_type else {}
    req = urllib.request.Request(base + path, data=data, headers=headers)
    with urllib.request.urlopen(req, timeout=30) as response:
        return json.load(response)


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--url', default='http://127.0.0.1:5175')
    parser.add_argument('--ffmpeg', default=os.environ.get('FFMPEG_BINARY', 'ffmpeg'))
    parser.add_argument('--ffprobe', default=os.environ.get('FFPROBE_BINARY', 'ffprobe'))
    args = parser.parse_args()
    root = Path(__file__).resolve().parents[1] / '.local-checks'
    root.mkdir(exist_ok=True)
    source = root / 'moving-test-pattern.mp4'
    subprocess.run([args.ffmpeg, '-hide_banner', '-loglevel', 'error', '-y',
                    '-f', 'lavfi', '-i', 'testsrc2=size=640x360:rate=30',
                    '-f', 'lavfi', '-i', 'sine=frequency=440:sample_rate=48000',
                    '-t', '8', '-c:v', 'libx264', '-preset', 'ultrafast',
                    '-pix_fmt', 'yuv420p', '-c:a', 'aac', str(source)], check=True)
    boundary = uuid.uuid4().hex
    payload = (f'--{boundary}\r\nContent-Disposition: form-data; name="file"; '
               'filename="moving-test-pattern.mp4"\r\nContent-Type: video/mp4\r\n\r\n').encode()
    payload += source.read_bytes() + f'\r\n--{boundary}--\r\n'.encode()
    uploaded = request(args.url, '/api/local-clips/sources', payload,
                       f'multipart/form-data; boundary={boundary}')
    report = {"source": "generated moving test pattern, not GTA gameplay", "exports": []}
    for layout, mute in [('fit', False), ('crop', True)]:
        job = request(args.url, '/api/local-clips/jobs', json.dumps({
            'source_id': uploaded['id'], 'clips': [
                {'start': 1, 'end': 6, 'hook': 'LOCAL EXPORT TEST'}],
            'layout': layout, 'mute': mute, 'focus': 30}).encode(), 'application/json')
        deadline = time.monotonic() + 120
        while time.monotonic() < deadline:
            state = request(args.url, f'/api/local-clips/jobs/{job["id"]}')
            if state['status'] in ('failed', 'interrupted'):
                raise RuntimeError(state)
            if state['status'] == 'completed':
                break
            time.sleep(1)
        else:
            raise TimeoutError('Local render did not finish within 120 seconds')
        output = root / f'test-export-{layout}.mp4'
        with urllib.request.urlopen(args.url + state['clips'][0]['url'], timeout=30) as response:
            output.write_bytes(response.read())
        meta = json.loads(subprocess.check_output([
            args.ffprobe, '-v', 'error', '-show_format', '-show_streams', '-of', 'json', str(output)]))
        video = next(s for s in meta['streams'] if s['codec_type'] == 'video')
        audio = any(s['codec_type'] == 'audio' for s in meta['streams'])
        duration = float(meta['format']['duration'])
        assert (video['width'], video['height']) == (1080, 1920), video
        assert abs(duration - 5) < .2, duration
        assert audio == (not mute), (audio, mute)
        subprocess.run([args.ffmpeg, '-v', 'error', '-i', str(output), '-f', 'null', '-'], check=True)
        report['exports'].append({'layout': layout, 'duration': duration, 'audio': audio,
                                   'width': video['width'], 'height': video['height'],
                                   'frame_rate': video['r_frame_rate'],
                                   'encoder': state['clips'][0]['encoder'], 'decode': 'passed'})
    (root / 'export-validation.json').write_text(json.dumps(report, indent=2), encoding='utf-8')
    print(json.dumps(report, indent=2))


if __name__ == '__main__':
    main()
