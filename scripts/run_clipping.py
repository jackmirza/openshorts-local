"""Background worker launched by Windows, independent of the setup terminal."""
import json
import os
from pathlib import Path
import sys

root = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(root))
runtime = root / '.local-checks'
config = json.loads((runtime / 'clipping-config.json').read_text(encoding='utf-8-sig'))
os.chdir(root)
os.environ['FFMPEG_BINARY'] = config['ffmpeg']
os.environ['FFPROBE_BINARY'] = config['ffprobe']
os.environ['FFMPEG_ENCODER'] = 'auto'
os.environ['LOCAL_CLIP_DIR'] = str(root / 'local-data')
os.environ['PATH'] = str(Path(config['ffmpeg']).parent) + os.pathsep + os.environ['PATH']
sys.stdout = (runtime / 'clipping.stdout.log').open('a', encoding='utf-8', buffering=1)
sys.stderr = (runtime / 'clipping.stderr.log').open('a', encoding='utf-8', buffering=1)

if __name__ == '__main__':
    import uvicorn
    uvicorn.run('local_clips_app:app', host='127.0.0.1', port=int(config['port']))
