import json
import tempfile
import unittest
from pathlib import Path
from app.diagnostics import Diagnostics

class DiagnosticTests(unittest.TestCase):
    def test_apple_diagnostics_do_not_store_tokens_or_raw_sdk_errors(self):
        with tempfile.TemporaryDirectory() as directory:
            Diagnostics(directory).write(7,{'source':'apple','events':[{'event':'apple-preview-blocked','duration':30,'metadata_duration':228,'authorized':True,'installed':True,'preview_only':False,'token':'private','message':'private'}]})
            p=Path(directory)/'playback-diagnostics/apple-7.json'
            self.assertNotIn('private',p.read_text())
            self.assertEqual(json.loads(p.read_text())['events'][0]['duration'],30)
            self.assertEqual(p.stat().st_mode&0o777,0o600)

    def test_payload_is_bounded_private_and_rejects_sensitive_fields(self):
        with tempfile.TemporaryDirectory() as directory:
            d=Diagnostics(directory)
            d.write(7,{'events':[{'event':'error','position':12,'profile':'original','paused':False,'url':'secret','email':'secret','token':'secret','cookie':'secret','duration':float('inf')},{'event':'arbitrary-secret'}]})
            p=Path(directory)/'playback-diagnostics/7.json';data=json.loads(p.read_text())
            self.assertEqual(len(data['events']),1)
            self.assertNotIn('secret',p.read_text());self.assertNotIn('duration',data['events'][0])
            self.assertEqual(p.stat().st_mode&0o777,0o600)
            d.write(8,{'events':[{'event':'progress','position':3}]})
            self.assertEqual(json.loads(p.read_text())['events'][0]['position'],12)
            with self.assertRaises(ValueError):d.write(7,{'events':[{}]*61})
            d.denied_stream(12)
            system=json.loads((Path(directory)/'playback-diagnostics/0.json').read_text())
            self.assertEqual(system['events'][0]['event'],'stream-auth-denied')
            self.assertEqual(system['events'][0]['track_id'],12)

    def test_dropout_context_is_numeric_and_private(self):
        with tempfile.TemporaryDirectory() as directory:
            d=Diagnostics(directory)
            events=[{'event':event,'buffered_ahead':.25,'network_state':2,'hidden':True,'wireless':True,'intent':True,'loading':False,'device_name':'private room','url':'secret'} for event in ('waiting','stalled','pause','visibility','route-change','airplay-end')]
            d.write(7,{'events':events})
            saved=json.loads((Path(directory)/'playback-diagnostics/7.json').read_text())['events']
            self.assertEqual(len(saved),6)
            self.assertEqual(saved[0]['buffered_ahead'],.25)
            self.assertTrue(saved[0]['hidden'])
            self.assertNotIn('private room',json.dumps(saved));self.assertNotIn('secret',json.dumps(saved))

    def test_audio_session_context_accepts_only_known_states(self):
        with tempfile.TemporaryDirectory() as directory:
            d=Diagnostics(directory)
            d.write(7,{'events':[{'event':'audio-session','audio_session_state':'interrupted','muted':True,'audio_volume':0.4},{'event':'audio-session','audio_session_state':'private speaker name'}]})
            saved=json.loads((Path(directory)/'playback-diagnostics/7.json').read_text())['events']
            self.assertEqual(saved[0]['audio_session_state'],'interrupted')
            self.assertTrue(saved[0]['muted']);self.assertEqual(saved[0]['audio_volume'],0.4)
            self.assertNotIn('audio_session_state',saved[1])

    def test_spotify_details_are_allowlisted_separate_and_private(self):
        with tempfile.TemporaryDirectory() as directory:
            d=Diagnostics(directory)
            d.write(7,{'events':[{'event':'playing','position':8}]})
            d.write(7,{'source':'spotify','events':[{'event':'spotify-playback-error','reason':'protection','message':'secret','device_id':'secret','track_id':'secret','url':'secret','wall_ms':123,'paused':True},{'event':'spotify-state','reason':'secret'},{'event':'spotify-state','reason':['secret']},{'event':['secret']}]})
            root=Path(directory)/'playback-diagnostics';p=root/'spotify-7.json';saved=json.loads(p.read_text())['events']
            self.assertEqual(len(saved),3);self.assertEqual(saved[0]['reason'],'protection')
            self.assertNotIn('secret',p.read_text());self.assertNotIn('reason',saved[1]);self.assertNotIn('reason',saved[2])
            self.assertEqual(p.stat().st_mode&0o777,0o600)
            self.assertEqual(json.loads((root/'7.json').read_text())['events'][0]['position'],8)
            with self.assertRaises(ValueError):d.write(7,{'source':'../../escape','events':[]})
            with self.assertRaises(ValueError):d.write(7,{'source':'spotify','events':[{}]*61})
