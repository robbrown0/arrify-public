import copy
import json
import tempfile
import unittest
from datetime import date
from pathlib import Path
from unittest.mock import patch

from hitdiscovery import engine
from hitdiscovery.config import Config
from hitdiscovery.matching import key
from hitdiscovery.sources import score
from hitdiscovery.store import Store


class DiscoveryDateTests(unittest.TestCase):
    def song(self, **fields):
        return dict(artist='Artist', title='Song', evidence=[dict(
            source='Chart', independent_family='Chart', rank=1, kind='current',
            date='2026-11-01', url='https://example.org/chart')], **fields)

    def test_unknown_and_malformed_dates_get_no_recency_bonus_or_mutation(self):
        for value in [None, '', ' ', '\t', 'unknown', '2026oops', '2026-13',
                      '2026-02-30', '2026-00-10', '2026-11-00', '0000',
                      '26', '20261127', 2026, 0, [], {}, True]:
            with self.subTest(value=value):
                song=self.song(release_date=value)
                before=copy.deepcopy(song)
                self.assertEqual(score(song), 75)
                self.assertEqual(song, before)
        song=self.song()
        self.assertEqual(score(song), 75)
        self.assertNotIn('release_date', song)

    def test_valid_partial_and_full_dates_keep_evidence_and_recency_scores(self):
        year=date.today().year
        for value in [str(year), f'{year}-01', f'{year}-01-01']:
            with self.subTest(value=value):
                self.assertEqual(score(self.song(release_date=value)), 85)
        self.assertEqual(score(self.song(release_date=f'{year-1}-01-01')), 83)
        self.assertEqual(score(self.song(release_date='1984-02-29')), 75)
        self.assertEqual(score(self.song(release_date=f'{year+1}-01-01')), 75)

    def test_evidence_scores_are_preserved_when_the_date_is_unknown(self):
        song=self.song(release_date='')
        song['evidence'].append(dict(song['evidence'][0], source='Other',
                                     independent_family='Other', date='2026-11-02'))
        self.assertEqual(score(song), 82)

    def test_full_run_defers_invalid_cached_dates_and_completes_valid_songs(self):
        with tempfile.TemporaryDirectory() as root:
            config=Config(data=Path(root)/'data', reports=Path(root)/'reports',
                          music_paths=(), catalog_db='', resolve_limit=0,
                          acquire=False, reviewed=False, expansion=False,
                          wikimedia=False)
            config.validate()
            invalid=[None, '', ' ', 'nonsense', '2026broken', '2026-02-30', 2026, {}, []]
            songs=[]
            for i, value in enumerate(invalid+['2020-01-01']):
                song=self.song(release_date=value)
                song.update(title=f'Song {i}', recording_id=f'recording-{i}',
                            release_group_id=f'album-{i}', release_id=f'edition-{i}',
                            artist_mbid=f'artist-{i}', album=f'Album {i}',
                            resolution_status='resolved')
                songs.append(song)
            store=Store(config.data/'discovery.sqlite3')
            for song in songs:
                store.cache('resolution:'+key(song['artist'], song['title']), song)
            store.db.close()
            candidates=[{k:v for k,v in s.items() if k not in (
                'release_date', 'resolution_status')} for s in songs]
            # Malformed hints must not fail candidate ranking either.
            for candidate, value in zip(candidates, invalid):
                candidate['release_date_hint']=value
            inventory=dict(lidarr_artists_with_files=0,
                           lidarr_albums_with_files=0, lidarr_owned_tracks=0)
            with patch.object(engine, 'inspect', return_value={'capabilities':{'version':'test'}}), \
                 patch.object(engine, 'scan', return_value=([], [], [], inventory)), \
                 patch.object(engine, 'discover', return_value=(candidates, [])), \
                 patch.object(engine, 'Lidarr') as lidarr, \
                 patch.object(engine, 'Resolver') as resolver, \
                 patch.object(engine, 'Acquirer') as acquirer, \
                 patch.object(engine, 'available_groups', return_value=[]):
                lidarr.return_value.get.return_value={'records':[]}
                resolver.return_value.album_release.return_value={}
                report=engine.run(config)
                resolver.return_value.resolve.assert_not_called()
                acquirer.return_value.request.assert_not_called()
            self.assertEqual(report['unresolved_count'], len(invalid))
            self.assertEqual(len(report['missing_songs']), 1)
            self.assertEqual(report['missing_by_year'], {'2020':[report['missing_songs'][0]]})
            self.assertTrue(all('release date' in s['reason'].lower() for s in report['unresolved']))
            self.assertEqual(json.loads((config.reports/'latest.json').read_text())['run_id'], report['run_id'])
            store=Store(config.data/'discovery.sqlite3')
            self.assertEqual(store.db.execute('SELECT status FROM runs').fetchone()[0], 'complete')
            for original in songs:
                cached=store.cached('resolution:'+key(original['artist'], original['title']))
                self.assertEqual(cached['release_date'], original['release_date'])
            store.db.close()


if __name__=='__main__':
    unittest.main()
