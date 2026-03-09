#!/usr/bin/env python3
"""Extract evaluation rows from a trace parquet file.

Usage:
  python extract_eval_rows.py <trace.parquet>

Outputs a JSON array of rows compatible with the `evaluations` table shape used
by the dashboard reconstruction path.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

import pyarrow.parquet as pq


def main() -> int:
    if len(sys.argv) != 2:
        print(json.dumps({"error": "usage: extract_eval_rows.py <trace.parquet>"}))
        return 1

    trace_path = Path(sys.argv[1])
    parquet = pq.ParquetFile(trace_path)
    rows: list[dict[str, object]] = []

    for batch in parquet.iter_batches(
        columns=["part", "turn", "eval_events_delta"],
        batch_size=256,
    ):
        batch_dict = batch.to_pydict()
        parts = batch_dict.get("part", [])
        turns = batch_dict.get("turn", [])
        raw_events = batch_dict.get("eval_events_delta", [])

        for index, value in enumerate(raw_events):
            if not isinstance(value, str) or len(value) <= 2:
                continue
            try:
                events = json.loads(value)
            except Exception:
                continue
            if not isinstance(events, list):
                continue

            part = int(parts[index]) if index < len(parts) and parts[index] is not None else 0
            turn = (
                int(turns[index])
                if index < len(turns) and turns[index] is not None
                else None
            )

            for event in events:
                if not isinstance(event, dict):
                    continue
                target_commit = event.get("target_commit")
                if not isinstance(target_commit, str) or not target_commit:
                    continue

                row = {
                    "part": part,
                    "turn": turn,
                    "eval_id": str(event.get("eval_id") or ""),
                    "status": str(event.get("status") or ""),
                    "passed": int(event.get("passed") or 0),
                    "failed": int(event.get("failed") or 0),
                    "total": int(event.get("total") or 0),
                    "target_commit": target_commit,
                    "suite_results": json.dumps(event.get("suite_results") or {}),
                    "finished_at": event.get("finished_at"),
                }
                rows.append(row)

    print(json.dumps(rows))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
