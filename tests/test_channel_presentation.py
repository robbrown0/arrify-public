import unittest
from app.channels import CHANNELS, present_channels


class ChannelPresentationTests(unittest.TestCase):
    def test_channel_order_logos_and_other_collection_positions(self):
        channels = [{'slug': slug, 'section': 'channels', 'tracks': 20,
                     'artwork': '/api/v1/artwork/1', 'artworks': ['/api/v1/artwork/1']}
                    for slug, _ in reversed(CHANNELS)]
        playlist = {'slug': 'personal-mix', 'section': 'playlists'}
        mood = {'slug': 'jazz', 'section': 'moods'}
        source = [playlist, *channels[:3], mood, *channels[3:]]
        result = present_channels(source)
        self.assertIs(result[0], playlist)
        self.assertIs(result[4], mood)
        actual = [item for item in result if item['section'] == 'channels']
        self.assertEqual([item['slug'] for item in actual], [slug for slug, _ in CHANNELS])
        for item, (_, asset) in zip(actual, CHANNELS):
            self.assertEqual(item['artwork'], '/assets/channels/' + asset + '.svg')
            self.assertEqual(item['artworks'], [item['artwork']])
            self.assertEqual(item['tracks'], 20)
        self.assertEqual(channels[0]['artwork'], '/api/v1/artwork/1')

    def test_unrecognized_channels_remain_after_requested_channels(self):
        unknown = {'slug': 'future-channel', 'section': 'channels'}
        known = {'slug': 'sirius-theblend', 'section': 'channels', 'tracks': 0}
        result = present_channels([unknown, known])
        self.assertEqual(result[0]['slug'], known['slug'])
        self.assertEqual(result[1], unknown)
        self.assertTrue(result[0]['channel_logo'])
