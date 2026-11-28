import importlib.util
from pathlib import Path
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[2]
spec = importlib.util.spec_from_file_location("ui_shards", ROOT / "ios/tools/ui_test_shards.py")
shards = importlib.util.module_from_spec(spec)
spec.loader.exec_module(shards)


class UIShardTests(unittest.TestCase):
    def test_full_partition_includes_reported_regressions_once(self):
        tests = shards.discover(ROOT / "ios/UITests")
        selected = [shards.select(tests, index, 2) for index in range(2)]
        self.assertEqual(sorted(selected[0] + selected[1]), tests)
        self.assertFalse(set(selected[0]) & set(selected[1]))
        for name in ["testTabletVolumeVisibleSliderStaysCenteredAfterLateLayoutAndRotation",
                     "testAudiobookListLoadsPagesRetriesAndKeepsPositionAfterDetails",
                     "testAudiobookActionsUnreadAndFinishedStayConsistentAcrossShelves",
                     "testHomeScrollPositionSurvivesFullScreenPlayerDismissal"]:
            self.assertIn("LaunchTests/" + name, tests)

    def test_unsupported_test_declarations_fail_closed(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            (root / "Cases.swift").write_text("final class Cases: XCTestCase {\n private func testHidden() {}\n}")
            with self.assertRaises(ValueError):
                shards.discover(root)

    def test_audit_requires_every_selected_runtime_method(self):
        expected = ["Cases/testOne", "Cases/testTwo"]
        log = "Test Case '-[ArrifyUITests.Cases testOne]' passed.\nExecuted 1 test, with 0 failures"
        with self.assertRaises(ValueError):
            shards.audit(log, expected)
        log += "\nTest Case '-[ArrifyUITests.Cases testTwo]' skipped.\nExecuted 2 tests, with 1 test skipped and 0 failures"
        shards.audit(log, expected)

    def test_audit_rejects_unexpected_tests_or_wrong_count(self):
        log = "Test Case '-[ArrifyUITests.Cases testOne]' passed.\nExecuted 2 tests, with 0 failures"
        with self.assertRaises(ValueError):
            shards.audit(log, ["Cases/testOne"])
        with self.assertRaises(ValueError):
            shards.audit(log, ["Cases/testOther"])
        with self.assertRaises(ValueError):
            shards.audit(log.replace("2 tests, with 0 failures", "1 test, with 1 failure"), ["Cases/testOne"])

    def test_empty_discovery_and_invalid_shards_fail(self):
        with tempfile.TemporaryDirectory() as folder:
            with self.assertRaises(ValueError):
                shards.discover(Path(folder))
        for index, count in [(2, 2), (-1, 2), (0, 3)]:
            with self.assertRaises(ValueError):
                shards.select(["Cases/testOne", "Cases/testTwo"], index, count)
