import json
from pathlib import Path
import unittest
import tempfile
from tools.audit_artist_names import aliases,audit,merge_names,apply

class ArtistNameAuditTests(unittest.TestCase):
    def setUp(self):self.manifest=json.loads(Path('ops/catalog/artist-name-aliases-20261102.json').read_text())
    def test_cure_sort_name_combines_track_sets_without_deleting_recordings(self):
        report=audit([{'id':1,'artist':'The Cure'},{'id':2,'artist':'Cure, The'}],self.manifest)
        self.assertEqual(report['tracks_audited'],2);self.assertEqual(report['artist_groups_projected'],1)
    def test_distinct_artists_are_never_merged_by_similarity(self):
        names=['Kingsmen, The','The Kingsmen','Kingsmen','Berlin Philharmonic Orchestra','Bergen Philharmonic Orchestra']
        report=audit([{'id':i,'artist':n} for i,n in enumerate(names)],self.manifest)
        self.assertEqual(report['artist_groups_before'],report['artist_groups_projected'])
    def test_existing_case_variants_and_old_targets_redirect_consistently(self):
        old={'Bruce Hungerford':'Bruce Hungerford,','Bruce Hungerford,':'Bruce Hungerford,','Edwin McCain':'Edwin Mccain','Edwin Mccain':'Edwin Mccain','Unrelated':'Unrelated'}
        result=merge_names(old,self.manifest)
        self.assertEqual(result['Bruce Hungerford'],'Bruce Hungerford');self.assertEqual(result['Edwin Mccain'],'Edwin McCain');self.assertEqual(result['Unrelated'],'Unrelated')
    def test_manifest_rejects_ambiguous_or_chained_targets(self):
        with self.assertRaises(ValueError):aliases({'corrections':[{'canonical':'A','aliases':['B'],'evidence':['reviewed']},{'canonical':'C','aliases':['A'],'evidence':['reviewed']}]})

    def test_apply_preserves_other_cleanup_and_keeps_exact_backup(self):
        with tempfile.TemporaryDirectory() as directory:
            path=Path(directory)/'cleanup.json';backup=Path(directory)/'before.json'
            original=b'{"names":{"Unrelated":"Unrelated"},"tracks":{"123":{"title":"Keep me"}}}'
            path.write_bytes(original)
            apply(path,self.manifest,backup)
            updated=json.loads(path.read_text())
            self.assertEqual(backup.read_bytes(),original)
            self.assertEqual(updated['tracks'],{'123':{'title':'Keep me'}})
            self.assertEqual(updated['names']['Cure, The'],'The Cure')
            applied=path.read_bytes()
            with self.assertRaises(FileExistsError):apply(path,self.manifest,backup)
            self.assertEqual(path.read_bytes(),applied)
