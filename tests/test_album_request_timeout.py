import unittest
from unittest.mock import patch, MagicMock
from app import requests

class AlbumRequestTimeoutTests(unittest.TestCase):
    def test_interactive_album_has_short_budget_without_changing_background_requests(self):
        for path, budget in [("/album-for-song?artist=A&title=B", 15), ("/search?q=A", 65)]:
            connection = MagicMock()
            connection.getresponse.return_value.status = 200
            connection.getresponse.return_value.read.return_value = b'{"items": []}'
            with patch.object(requests, "UnixHTTP", return_value=connection) as factory:
                self.assertEqual(requests.call(path), {"items": []})
                factory.assert_called_once_with("localhost", timeout=budget)
                connection.close.assert_called_once()

    def test_timeout_closes_connection_and_returns_retriable_error(self):
        connection = MagicMock()
        connection.getresponse.side_effect = TimeoutError()
        with patch.object(requests, "UnixHTTP", return_value=connection):
            with self.assertRaisesRegex(ValueError, "temporarily unavailable"):
                requests.call("/album-for-song?artist=A&title=B")
        connection.close.assert_called_once()
