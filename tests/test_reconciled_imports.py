import json
from pathlib import Path
import tempfile
import unittest
from app.library import verified_import_identity
from app.request_progress import mark_background_requested
from app.browse import chart_recording_key


class ImportIdentityTests(unittest.TestCase):
    def test_proof_only_restores_unchanged_path_derived_identity(self):
        row = dict(metadata_source='path',source='extra',relative_path='one.mp3',modified_ns=5,
                   title='Artist - Album - 01 - Song')
        proof = dict(row,artist='Artist',title='Song')
        self.assertEqual(verified_import_identity(row,proof),{'artist':'Artist','title':'Song'})
        for changed in (dict(row,modified_ns=6),dict(row,source='nas01'),
                        dict(row,metadata_source='tags'),dict(row,title='Real title')):
            with self.subTest(changed=changed):self.assertIsNone(verified_import_identity(changed,proof))

    def test_featured_credit_location_matches_but_versions_stay_distinct(self):
        self.assertEqual(chart_recording_key({'artist':'Artist','title':'Song (feat. Guest)'}),
                         chart_recording_key({'artist':'Artist & Guest','title':'Song'}))
        self.assertNotEqual(chart_recording_key({'artist':'Artist','title':'Song'}),
                            chart_recording_key({'artist':'Artist','title':'Song (Live)'}))

    def test_request_markers_expire_and_do_not_match_other_versions(self):
        with tempfile.TemporaryDirectory() as directory:
            path=Path(directory)/'markers.json'
            path.write_text(json.dumps({'updated':100,'items':[{'artist':'Artist & Guest','title':'Song'}]}))
            def items():return [{'artist':'Artist and Guest','title':'Song'},
                                {'artist':'Artist and Guest','title':'Song (Live)'}]
            marked=mark_background_requested(items(),path,now=lambda:101)
            self.assertTrue(marked[0]['requested']);self.assertNotIn('requested',marked[1])
            self.assertNotIn('requested',mark_background_requested(items(),path,now=lambda:3700)[0])
            path.write_text('broken JSON')
            self.assertNotIn('requested',mark_background_requested(items(),path,now=lambda:101)[0])
