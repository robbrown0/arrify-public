"""Real FFmpeg audio measurements; all source/output files are disposable fixtures."""
import hashlib
import json
import math
import re
import subprocess
import tempfile
import unittest
from pathlib import Path
from app.sound import safe_gain

def measure(path):
    r=subprocess.run(['ffmpeg','-hide_banner','-nostats','-i',str(path),'-af','loudnorm=I=-16:TP=-1:LRA=50:print_format=json','-f','null','-'],stdout=subprocess.DEVNULL,stderr=subprocess.PIPE,check=True)
    return json.loads(re.findall(rb'\{[^{}]*"input_i"[^{}]*\}',r.stderr)[-1])

class RenderTests(unittest.TestCase):
    def test_real_loud_quiet_tracks_match_without_source_changes(self):
        with tempfile.TemporaryDirectory() as d:
            outputs=[]
            for label,volume in [('quiet',.5),('loud',2)]:
                source=Path(d)/(label+'.wav');output=Path(d)/(label+'-leveled.flac')
                subprocess.run(['ffmpeg','-v','error','-f','lavfi','-i','sine=frequency=440:sample_rate=44100:duration=3','-af','volume='+str(volume),'-ac','2',str(source)],check=True)
                before=hashlib.sha256(source.read_bytes()).digest()
                m=measure(source);gain=safe_gain(float(m['input_i']),float(m['input_tp']))
                subprocess.run(['ffmpeg','-v','error','-i',str(source),'-af','volume='+str(gain)+'dB',str(output)],check=True)
                result=measure(output);outputs.append(float(result['input_i']))
                self.assertLessEqual(float(result['input_tp']),-1)
                self.assertEqual(hashlib.sha256(source.read_bytes()).digest(),before)
            self.assertLess(abs(outputs[0]-outputs[1]),.2)
            for value in outputs:self.assertLess(abs(value+16),.2)
