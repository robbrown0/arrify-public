#!/usr/bin/env python3
"""Partition every XCTest UI method and audit the methods that actually ran."""
import argparse
from pathlib import Path
import re


def discover(root: Path) -> list[str]:
    tests = []
    for path in sorted(root.rglob("*.swift")):
        source = path.read_text()
        declared = re.findall(r"\bfunc\s+(test\w+)\s*\(", source)
        if not declared:
            continue
        classes = re.findall(r"\bclass\s+(\w+)\s*:\s*XCTestCase\b", source)
        methods = re.findall(r"(?m)^\s*(?:@MainActor\s+)?(?:public\s+|internal\s+)?func\s+(test\w+)\s*\(", source)
        if len(classes) != 1 or sorted(methods) != sorted(declared):
            raise ValueError(f"Cannot safely discover every UI test in {path}")
        tests.extend(f"{classes[0]}/{method}" for method in methods)
    if not tests or len(tests) != len(set(tests)):
        raise ValueError("UI test discovery must be nonempty and unique")
    return sorted(tests)


def select(tests: list[str], shard: int, count: int) -> list[str]:
    if not 0 <= shard < count or count > len(tests):
        raise ValueError("Invalid or empty UI shard")
    return tests[shard::count]


def audit(log: str, expected: list[str]) -> None:
    observed = {f"{suite}/{method}" for suite, method in
                re.findall(r"-\[(?:ArrifyUITests\.)?(\w+) (test\w+)\]", log)}
    counts = re.findall(r"Executed (\d+) tests?, with (?:\d+ tests? skipped and )?(\d+) failures?", log)
    if (observed != set(expected) or not counts or int(counts[-1][0]) != len(expected)
            or int(counts[-1][1]) != 0):
        raise ValueError(f"UI coverage mismatch: missing={sorted(set(expected)-observed)}, "
                         f"extra={sorted(observed-set(expected))}, expected_count={len(expected)}")


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root", type=Path, default=Path(__file__).resolve().parents[1] / "UITests")
    parser.add_argument("--shard", type=int, required=True)
    parser.add_argument("--count", type=int, default=2)
    parser.add_argument("--audit-log", type=Path)
    args = parser.parse_args()
    tests = discover(args.root)
    shards = [select(tests, i, args.count) for i in range(args.count)]
    if sorted(test for part in shards for test in part) != tests:
        raise ValueError("UI partition omitted or duplicated a test")
    selected = select(tests, args.shard, args.count)
    if args.audit_log:
        audit(args.audit_log.read_text(errors="replace"), selected)
        print(f"UI coverage verified: {len(selected)}/{len(tests)} methods in shard {args.shard+1}/{args.count}")
    else:
        for test in selected:
            print("-only-testing:ArrifyUITests/" + test)


if __name__ == "__main__":
    main()
