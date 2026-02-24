from __future__ import annotations

import io

from envoi_code.utils.logs_parquet import (
    log_records_to_rows,
    read_logs_parquet,
    write_logs_parquet,
)


def test_logs_parquet_roundtrip() -> None:
    records = [
        {
            "ts": "2026-02-24T08:00:00+00:00",
            "component": "orchestrator",
            "event": "progress",
            "level": "info",
            "message": "turn completed",
            "turn": 2,
            "part": 123,
            "git_commit": "abc123",
            "session_id": "sess-1",
            "source": "orchestrator",
            "extra_field": {"foo": "bar"},
        },
    ]

    rows = log_records_to_rows("traj-1", records)
    assert len(rows) == 1
    assert rows[0]["trajectory_id"] == "traj-1"
    assert rows[0]["fields"] is not None

    buf = io.BytesIO()
    write_logs_parquet(rows, buf)
    buf.seek(0)
    parsed = read_logs_parquet(buf)

    assert len(parsed) == 1
    assert parsed[0]["trajectory_id"] == "traj-1"
    assert parsed[0]["component"] == "orchestrator"
    assert parsed[0]["event"] == "progress"
