"""Structured diagnostics extraction, rendering, and clustering."""

from __future__ import annotations

import re
from typing import Any

Severity = str

_HEADER_RE = re.compile(
    r"^(?P<severity>error|warning|note|help)(?:\[(?P<code>[A-Za-z]\d+)\])?:\s*(?P<message>.+)$"
)
_RUST_LOCATION_RE = re.compile(
    r"^\s*-->\s*(?P<file>.+?):(?P<line>\d+):(?P<col>\d+)\s*$"
)
_RUST_LABEL_RE = re.compile(
    r"^\s*\|\s*[~^\-]+\s*(?P<label>.+)$"
)
_GCC_RE = re.compile(
    r"^(?P<file>[^:\n]+):(?P<line>\d+):(?P<col>\d+)(?::(?P<endcol>\d+))?:\s*"
    r"(?P<severity>fatal error|error|warning|note):\s*(?P<message>.+)$"
)
_BYTE_OFFSET_RE = re.compile(
    r"(?P<prefix>.+?)\s+at\s+byte\s+(?P<offset>\d+)\b"
)
_NORMALIZE_INT_RE = re.compile(r"\b\d+\b")
_NORMALIZE_HEX_RE = re.compile(r"\b0x[0-9a-fA-F]+\b")
_NORMALIZE_QUOTED_RE = re.compile(r"'[^']+'|\"[^\"]+\"")


def _str_or_none(value: Any) -> str | None:
    if not isinstance(value, str):
        return None
    stripped = value.strip()
    return stripped if stripped else None


def _int_or_default(value: object, *, default: int = 0) -> int:
    return value if isinstance(value, int) else default


def _int_or_none(value: object) -> int | None:
    return value if isinstance(value, int) else None


def _clip(text: str, limit: int = 240) -> str:
    if len(text) <= limit:
        return text
    return text[:limit].rstrip() + " ..."


def _normalize_kind(failure_type: str | None, message: str | None) -> str:
    text = (message or "").lower()
    failure = (failure_type or "").lower()
    if "undefined reference" in text or "linker failed" in text:
        return "linker_error"
    if "symbol" in text and "already defined" in text:
        return "linker_error"
    if "expected" in text and "byte" in text:
        return "parse_error"
    if "unexpected token" in text:
        return "parse_error"
    if failure == "compile_error":
        return "compile_error"
    if failure == "runtime_error":
        return "runtime_error"
    if failure == "assertion":
        return "assertion_error"
    if failure in {"error", "timeout"}:
        return failure
    return "evaluation_error"


def _offset_to_line_col(source: str, offset: int) -> tuple[int, int]:
    bounded = max(0, min(offset, len(source)))
    line = 1
    col = 1
    for ch in source[:bounded]:
        if ch == "\n":
            line += 1
            col = 1
        else:
            col += 1
    return line, col


def _line_at(source: str, line_no: int) -> str:
    lines = source.splitlines()
    if line_no <= 0 or line_no > len(lines):
        return ""
    return lines[line_no - 1]


def _render_primary_span(
    *,
    source: str,
    line: int,
    col: int,
    end_col: int | None,
    label: str | None,
) -> str:
    if not source:
        return ""
    target = _line_at(source, line)
    if not target:
        return ""

    width = max(2, len(str(line)))
    marker_col = max(1, col)
    span = (
        max(1, (end_col - marker_col))
        if isinstance(end_col, int) and end_col > marker_col
        else 1
    )
    marker = " " * (marker_col - 1) + "^" * span
    if label:
        marker += f" {label}"
    return "\n".join(
        [
            f"{line:>{width}} | {target}",
            f"{'':>{width}} | {marker}",
        ]
    )


def _render_diagnostic(
    diagnostic: dict[str, Any],
    *,
    source: str | None,
) -> str:
    severity = _str_or_none(diagnostic.get("severity")) or "error"
    code = _str_or_none(diagnostic.get("code"))
    message = _str_or_none(diagnostic.get("message")) or "diagnostic"
    primary = diagnostic.get("primary")
    header = (
        f"{severity}[{code}]: {message}"
        if code
        else f"{severity}: {message}"
    )
    lines = [header]
    if isinstance(primary, dict):
        file = _str_or_none(primary.get("file")) or "<source>"
        line = _int_or_default(primary.get("line"), default=0)
        col = _int_or_default(primary.get("col"), default=0)
        end_col = _int_or_none(primary.get("end_col"))
        lines.append(f" --> {file}:{line}:{col}")
        if source:
            lines.append(
                _render_primary_span(
                    source=source,
                    line=line,
                    col=col,
                    end_col=end_col,
                    label=_str_or_none(primary.get("label")),
                )
            )
    notes = diagnostic.get("notes")
    if isinstance(notes, list):
        for note in notes:
            note_text = _str_or_none(note)
            if note_text:
                lines.append(f" note: {note_text}")
    help_list = diagnostic.get("help")
    if isinstance(help_list, list):
        for hint in help_list:
            hint_text = _str_or_none(hint)
            if hint_text:
                lines.append(f" help: {hint_text}")
    return "\n".join(line for line in lines if line).strip()


def _normalize_message_template(message: str) -> str:
    value = _NORMALIZE_HEX_RE.sub("<hex>", message)
    value = _NORMALIZE_INT_RE.sub("<n>", value)
    value = _NORMALIZE_QUOTED_RE.sub("<q>", value)
    value = re.sub(r"\s+", " ", value).strip().lower()
    return value


def _cluster_key(
    *,
    kind: str,
    code: str | None,
    message: str,
) -> str:
    template = _normalize_message_template(message)
    return f"{kind}|{code or '-'}|{template}"


def _build_diag(
    *,
    severity: Severity,
    kind: str,
    message: str,
    code: str | None = None,
    primary: dict[str, Any] | None = None,
    secondary: list[dict[str, Any]] | None = None,
    notes: list[str] | None = None,
    help_list: list[str] | None = None,
    source_origin: str,
    confidence: float,
    source: str | None,
) -> dict[str, Any]:
    cluster = _cluster_key(
        kind=kind,
        code=code,
        message=message,
    )
    diagnostic: dict[str, Any] = {
        "severity": severity,
        "kind": kind,
        "message": message,
        "code": code,
        "primary": primary,
        "secondary": secondary or [],
        "notes": notes or [],
        "help": help_list or [],
        "source_origin": source_origin,
        "confidence": max(0.0, min(1.0, confidence)),
        "cluster_key": cluster,
    }
    diagnostic["rendered"] = _render_diagnostic(
        diagnostic,
        source=source,
    )
    return diagnostic


def _parse_rust_style(
    text: str,
    *,
    source: str | None,
) -> list[dict[str, Any]]:
    lines = text.splitlines()
    out: list[dict[str, Any]] = []
    idx = 0
    while idx < len(lines):
        header_match = _HEADER_RE.match(lines[idx].strip())
        if not header_match:
            idx += 1
            continue

        severity = header_match.group("severity")
        code = _str_or_none(header_match.group("code"))
        message = _str_or_none(header_match.group("message")) or "error"

        block_start = idx
        idx += 1
        while idx < len(lines) and not _HEADER_RE.match(lines[idx].strip()):
            idx += 1
        block = lines[block_start:idx]
        primary: dict[str, Any] | None = None
        notes: list[str] = []
        for block_line in block:
            location_match = _RUST_LOCATION_RE.match(block_line)
            if location_match and primary is None:
                primary = {
                    "file": location_match.group("file").strip(),
                    "line": int(location_match.group("line")),
                    "col": int(location_match.group("col")),
                    "label": None,
                }
                continue
            label_match = _RUST_LABEL_RE.match(block_line)
            if label_match and primary is not None and primary.get("label") is None:
                primary["label"] = label_match.group("label").strip()
                continue
            stripped = block_line.strip()
            if stripped.startswith("="):
                notes.append(stripped.lstrip("= ").strip())

        out.append(
            _build_diag(
                severity=severity,
                kind=_normalize_kind("compile_error", message),
                message=message,
                code=code,
                primary=primary,
                notes=notes,
                source_origin="stderr_parser",
                confidence=0.95,
                source=source,
            )
        )
    return out


def _parse_gcc_clang_style(
    text: str,
    *,
    source: str | None,
) -> list[dict[str, Any]]:
    out: list[dict[str, Any]] = []
    for line in text.splitlines():
        match = _GCC_RE.match(line.strip())
        if not match:
            continue
        severity = match.group("severity").replace("fatal ", "")
        message = _str_or_none(match.group("message")) or "error"
        col = int(match.group("col"))
        end_col_value = match.group("endcol")
        end_col = int(end_col_value) if end_col_value is not None else None
        out.append(
            _build_diag(
                severity=severity,
                kind=_normalize_kind("compile_error", message),
                message=message,
                primary={
                    "file": match.group("file").strip(),
                    "line": int(match.group("line")),
                    "col": col,
                    "end_col": end_col,
                    "label": None,
                },
                source_origin="stderr_parser",
                confidence=0.92,
                source=source,
            )
        )
    return out


def _parse_byte_offset_style(
    text: str,
    *,
    source: str | None,
    default_file: str,
) -> list[dict[str, Any]]:
    out: list[dict[str, Any]] = []
    for line in text.splitlines():
        match = _BYTE_OFFSET_RE.search(line)
        if not match:
            continue
        if not source:
            continue
        message = _str_or_none(line) or "error"
        offset = int(match.group("offset"))
        line_no, col = _offset_to_line_col(source, offset)
        out.append(
            _build_diag(
                severity="error",
                kind="parse_error",
                message=message,
                primary={
                    "file": default_file,
                    "line": line_no,
                    "col": col,
                    "label": "byte offset from compiler message",
                },
                source_origin="heuristic",
                confidence=0.82,
                source=source,
            )
        )
    return out


def _parse_linker_runtime(
    text: str,
    *,
    source: str | None,
) -> list[dict[str, Any]]:
    lowered = text.lower()
    out: list[dict[str, Any]] = []
    if "undefined reference" in lowered:
        out.append(
            _build_diag(
                severity="error",
                kind="linker_error",
                message=_clip(text),
                source_origin="heuristic",
                confidence=0.9,
                source=source,
            )
        )
    if "already defined" in lowered and "symbol" in lowered:
        out.append(
            _build_diag(
                severity="error",
                kind="linker_error",
                message=_clip(text),
                source_origin="heuristic",
                confidence=0.9,
                source=source,
            )
        )
    if "exit code mismatch" in lowered or "stdout mismatch" in lowered:
        out.append(
            _build_diag(
                severity="error",
                kind="assertion_error",
                message=_clip(text),
                source_origin="heuristic",
                confidence=0.88,
                source=source,
            )
        )
    return out


def _dedupe_diagnostics(items: list[dict[str, Any]]) -> list[dict[str, Any]]:
    out: list[dict[str, Any]] = []
    seen: set[tuple[str, str, str | None, str | None]] = set()
    for item in items:
        if not isinstance(item, dict):
            continue
        primary = item.get("primary")
        primary_key = None
        if isinstance(primary, dict):
            primary_key = (
                f"{primary.get('file')}:{primary.get('line')}:{primary.get('col')}"
            )
        key = (
            _str_or_none(item.get("severity")) or "error",
            _str_or_none(item.get("kind")) or "evaluation_error",
            _str_or_none(item.get("code")),
            (_str_or_none(item.get("message")) or "") + "|" + (primary_key or "-"),
        )
        if key in seen:
            continue
        seen.add(key)
        out.append(item)
    return out


def extract_test_diagnostics(test: dict[str, Any]) -> list[dict[str, Any]]:
    """Extract structured diagnostics for a single test result."""
    status = (_str_or_none(test.get("status")) or "failed").lower()
    if status == "passed":
        return []

    existing = test.get("diagnostics")
    source = _str_or_none(test.get("source"))
    if isinstance(existing, list) and existing:
        parsed_existing = [
            item for item in existing if isinstance(item, dict)
        ]
        if parsed_existing:
            for item in parsed_existing:
                if "rendered" not in item:
                    item["rendered"] = _render_diagnostic(
                        item,
                        source=source,
                    )
                if "cluster_key" not in item:
                    kind = _str_or_none(item.get("kind")) or "evaluation_error"
                    code = _str_or_none(item.get("code"))
                    message = _str_or_none(item.get("message")) or "error"
                    item["cluster_key"] = _cluster_key(
                        kind=kind,
                        code=code,
                        message=message,
                    )
                if "source_origin" not in item:
                    item["source_origin"] = "compiler_json"
                if "confidence" not in item:
                    item["confidence"] = 0.98
            return _dedupe_diagnostics(parsed_existing)

    message = _str_or_none(test.get("message"))
    stderr_tail = _str_or_none(test.get("stderr_tail"))
    stdout_tail = _str_or_none(test.get("stdout_tail"))
    default_file = (_str_or_none(test.get("test_id")) or "test") + ".c"
    combined_parts = [part for part in [message, stderr_tail, stdout_tail] if part]
    combined = "\n".join(combined_parts)
    failure_type = _str_or_none(test.get("failure_type"))

    diagnostics: list[dict[str, Any]] = []
    if combined:
        diagnostics.extend(
            _parse_rust_style(
                combined,
                source=source,
            )
        )
        diagnostics.extend(
            _parse_gcc_clang_style(
                combined,
                source=source,
            )
        )
        diagnostics.extend(
            _parse_byte_offset_style(
                combined,
                source=source,
                default_file=default_file,
            )
        )
        diagnostics.extend(
            _parse_linker_runtime(
                combined,
                source=source,
            )
        )

    diagnostics = _dedupe_diagnostics(diagnostics)
    if diagnostics:
        return diagnostics

    fallback_message = (
        message
        or stderr_tail
        or stdout_tail
        or "Test failed without diagnostic output."
    )
    kind = _normalize_kind(failure_type, fallback_message)
    return [
        _build_diag(
            severity="error",
            kind=kind,
            message=fallback_message,
            source_origin="heuristic",
            confidence=0.5,
            source=source,
        )
    ]


def cluster_from_tests(
    tests: list[dict[str, Any]],
    *,
    max_samples: int = 3,
) -> list[dict[str, Any]]:
    buckets: dict[str, dict[str, Any]] = {}
    for test in tests:
        if not isinstance(test, dict):
            continue
        status = (_str_or_none(test.get("status")) or "failed").lower()
        if status == "passed":
            continue
        diagnostics = test.get("diagnostics")
        if not isinstance(diagnostics, list) or not diagnostics:
            continue

        suite = _str_or_none(test.get("suite")) or "unknown_suite"
        test_id = _str_or_none(test.get("test_id")) or "unknown_test"
        for diag in diagnostics:
            if not isinstance(diag, dict):
                continue
            key = _str_or_none(diag.get("cluster_key"))
            if key is None:
                kind = _str_or_none(diag.get("kind")) or "evaluation_error"
                code = _str_or_none(diag.get("code"))
                message = _str_or_none(diag.get("message")) or "error"
                key = _cluster_key(kind=kind, code=code, message=message)
            bucket = buckets.get(key)
            if bucket is None:
                bucket = {
                    "key": key,
                    "kind": _str_or_none(diag.get("kind")) or "evaluation_error",
                    "code": _str_or_none(diag.get("code")),
                    "count": 0,
                    "suites": set(),
                    "sample_tests": [],
                }
                buckets[key] = bucket
            bucket["count"] += 1
            bucket["suites"].add(suite)
            sample = f"{suite}/{test_id}"
            if (
                isinstance(bucket["sample_tests"], list)
                and sample not in bucket["sample_tests"]
                and len(bucket["sample_tests"]) < max_samples
            ):
                bucket["sample_tests"].append(sample)

    clusters: list[dict[str, Any]] = []
    for bucket in buckets.values():
        clusters.append(
            {
                "key": bucket["key"],
                "kind": bucket["kind"],
                "code": bucket["code"],
                "count": int(bucket["count"]),
                "suites": sorted(bucket["suites"]),
                "sample_tests": list(bucket["sample_tests"]),
            }
        )
    clusters.sort(
        key=lambda item: (
            -int(item.get("count", 0)),
            _str_or_none(item.get("kind")) or "",
            _str_or_none(item.get("key")) or "",
        )
    )
    return clusters


def enrich_evaluation_payload(payload: dict[str, Any]) -> dict[str, Any]:
    """Mutate evaluation payload with structured diagnostics and clusters."""
    tests = payload.get("tests")
    if not isinstance(tests, list):
        payload["diagnostic_clusters"] = []
        return payload

    normalized_tests: list[dict[str, Any]] = []
    for item in tests:
        if not isinstance(item, dict):
            continue
        diagnostics = extract_test_diagnostics(item)
        item["diagnostics"] = diagnostics
        item["rendered_diagnostic"] = (
            diagnostics[0].get("rendered")
            if diagnostics
            else None
        )
        item["cluster_key"] = (
            diagnostics[0].get("cluster_key")
            if diagnostics
            else None
        )
        normalized_tests.append(item)

    payload["tests"] = normalized_tests
    payload["diagnostic_clusters"] = cluster_from_tests(normalized_tests)
    return payload
