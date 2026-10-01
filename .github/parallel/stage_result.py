#!/usr/bin/env python3
from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(Path(__file__).resolve().parent))

from common import ALL_TARGETS, copy_target_output, read_json, status_path, write_json


def main() -> int:
    if len(sys.argv) != 4 or sys.argv[1] not in ALL_TARGETS:
        print("usage: stage_result.py <target> <build-outcome> <finalize-outcome>", file=sys.stderr)
        return 2
    target, build_outcome, finalize_outcome = sys.argv[1:]
    out = ROOT / ".parallel" / "out" / target
    out.mkdir(parents=True, exist_ok=True)
    fragment = read_json(status_path(target))
    success = build_outcome == "success" and finalize_outcome == "success"
    fragment["worker_success"] = success
    fragment["build_outcome"] = build_outcome
    fragment["finalize_outcome"] = finalize_outcome
    if success:
        copy_target_output(target, out)
    write_json(out / "status" / f"{target}.json", fragment)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
