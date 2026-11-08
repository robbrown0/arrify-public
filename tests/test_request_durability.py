import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch
from hitdiscovery import request_service as service

class RequestDurabilityTests(unittest.TestCase):
    def test_accepted_inbox_fsyncs_payload_and_directory(self):
        with tempfile.TemporaryDirectory() as folder:
            root=Path(folder);(root/'inbox').mkdir();target=root/'inbox'/'request.json'
            with patch.object(service,'ROOT',root),patch.object(service.os,'fsync',wraps=service.os.fsync) as sync:service.atomic(target,{'id':'request'})
            self.assertEqual(sync.call_count,2);self.assertEqual(json.loads(target.read_text()),{'id':'request'});self.assertFalse(target.with_suffix('.tmp').exists())
    def test_derivative_progress_avoids_unnecessary_durable_flushes(self):
        with tempfile.TemporaryDirectory() as folder:
            root=Path(folder);(root/'status').mkdir()
            with patch.object(service,'ROOT',root),patch.object(service.os,'fsync') as sync:service.atomic(root/'status'/'request.json',{'state':'queued'})
            sync.assert_not_called()
