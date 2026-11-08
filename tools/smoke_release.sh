#!/usr/bin/env bash
# Disposable, isolated server: no production data, published ports, or network.
set -euo pipefail
image=${1:?image required}
revision=${2:-unknown}
container="arrify-smoke-$$"
cleanup() { docker rm -f "$container" >/dev/null 2>&1 || true; }
trap cleanup EXIT

docker run -d --name "$container" --network none --read-only \
  --cap-drop ALL --security-opt no-new-privileges --pids-limit 100 --memory 512m \
  --tmpfs /data:rw,size=128m,uid=21012,gid=21012,mode=0700 \
  --tmpfs /requests:rw,size=16m,uid=21012,gid=21012,mode=0700 \
  --tmpfs /tmp:rw,size=64m,mode=1777 \
  -e ARRIFY_BROWSE_INDEX=0 "$image" >/dev/null
if ! docker exec -i "$container" python3 - "$revision" <<'PYTHON'
import json, os, subprocess, sys, time, urllib.request
assert os.getuid() == 21012
for attempt in range(30):
    try:
        with urllib.request.urlopen('http://127.0.0.1:8080/health', timeout=2) as response:
            health = json.load(response)
        break
    except OSError:
        time.sleep(1)
else:
    raise RuntimeError('Server did not become healthy')
assert health['status'] == 'ok' and health['catalog_tracks'] == 0, health
assert health['build']['revision'] == sys.argv[1], health
for codec, extension in [('libmp3lame', 'mp3'), ('aac', 'm4a'), ('flac', 'flac')]:
    path = '/tmp/smoke.' + extension
    subprocess.run(['ffmpeg','-v','error','-nostdin','-f','lavfi','-i',
                    'sine=frequency=440:duration=1','-c:a',codec,path], check=True)
    probe = subprocess.check_output(['ffprobe','-v','error','-show_format','-of','json',path])
    assert float(json.loads(probe)['format']['duration']) >= 1
    subprocess.run(['ffmpeg','-v','error','-nostdin','-i',path,'-f','null','-'], check=True)
subprocess.run(['openssl','version'], check=True)
print(json.dumps({'health': health, 'audio_smoke': 'MP3/AAC/FLAC encode, probe, decode passed'}))
PYTHON
then
  docker logs --tail 60 "$container"
  exit 1
fi
