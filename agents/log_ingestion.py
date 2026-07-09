import json
import re
from pathlib import Path
from typing import Any, Iterable


LEVELS: set[str] = {"TRACE", "DEBUG", "INFO", "NOTICE", "WARN", "WARNING", "ERROR", "CRITICAL", "FATAL"}

ISO_TS_RE = re.compile(r"(\d{4}-\d{2}-\d{2}[T ][0-9:.]+(?:Z|[+-]\d{2}:?\d{2})?)")
SYSLOG_TS_RE = re.compile(r"([A-Z][a-z]{2}\s+\d{1,2}\s+[0-9:]{8})")
HDFS_TS_RE = re.compile(r"(\d{6}\s+\d{6})")
APACHE_STATUS_RE = re.compile(r'"\w+\s+[^"]+\s+HTTP/[^"]+"\s+(\d{3})')


def load_log_file(path: str | Path, service: str = "unknown") -> list[dict[str, Any]]:
    source_path: Path = Path(path).expanduser()
    if not source_path.exists() or not source_path.is_file():
        raise FileNotFoundError(f"log source not found: {source_path}")

    suffix: str = source_path.suffix.lower()
    if suffix == ".json":
        return _load_json(source_path, service)
    if suffix in {".jsonl", ".ndjson"}:
        return _load_jsonl(source_path, service)
    return _load_plain(source_path, service)


def _load_json(path: Path, service: str) -> list[dict[str, Any]]:
    with path.open("r", encoding="utf-8") as handle:
        data: Any = json.load(handle)
    if isinstance(data, dict):
        for key in ("logs", "records", "events", "data"):
            if isinstance(data.get(key), list):
                data = data[key]
                break
    if not isinstance(data, list):
        raise ValueError("JSON log source must be an array or object with logs/records/events/data")
    return [_normalize_entry(item, service, str(index)) for index, item in enumerate(data)]


def _load_jsonl(path: Path, service: str) -> list[dict[str, Any]]:
    entries: list[dict[str, Any]] = []
    with path.open("r", encoding="utf-8") as handle:
        for index, line in enumerate(handle):
            text: str = line.strip()
            if not text:
                continue
            try:
                item: Any = json.loads(text)
            except json.JSONDecodeError:
                item = {"message": text}
            entries.append(_normalize_entry(item, service, str(index)))
    return entries


def _load_plain(path: Path, service: str) -> list[dict[str, Any]]:
    entries: list[dict[str, Any]] = []
    with path.open("r", encoding="utf-8", errors="replace") as handle:
        for index, line in enumerate(handle):
            text: str = line.strip()
            if not text:
                continue
            entries.append(_normalize_line(text, service, str(index)))
    return entries


def _normalize_entry(item: Any, service: str, fallback_timestamp: str) -> dict[str, Any]:
    if not isinstance(item, dict):
        return _normalize_line(str(item), service, fallback_timestamp)

    timestamp: str = _first_string(
        item,
        ("timestamp", "time", "@timestamp", "datetime", "date", "event_time"),
        fallback_timestamp,
    )
    level: str = _normalize_level(
        _first_string(item, ("level", "severity", "log_level", "priority"), "")
    )
    message: str = _first_string(
        item,
        ("message", "msg", "log", "event", "text", "content", "raw"),
        json.dumps(item, default=str),
    )
    parsed_service: str = _first_string(
        item,
        ("service", "app", "application", "component", "source", "logger", "host"),
        service,
    )
    if not level:
        level = _infer_level(message)

    entry: dict[str, Any] = {
        "timestamp": timestamp,
        "level": level,
        "service": parsed_service or service,
        "message": message,
    }
    error_type: str = _first_string(item, ("error_type", "exception", "error", "type"), "")
    if not error_type:
        error_type = _infer_error_type(message)
    if error_type:
        entry["error_type"] = error_type
    return entry


def _normalize_line(line: str, service: str, fallback_timestamp: str) -> dict[str, Any]:
    timestamp: str = _extract_timestamp(line) or fallback_timestamp
    level: str = _infer_level(line)
    if level == "INFO":
        status_match = APACHE_STATUS_RE.search(line)
        if status_match and int(status_match.group(1)) >= 500:
            level = "ERROR"

    entry: dict[str, Any] = {
        "timestamp": timestamp,
        "level": level,
        "service": service,
        "message": line,
    }
    error_type: str = _infer_error_type(line)
    if error_type:
        entry["error_type"] = error_type
    return entry


def _first_string(item: dict[str, Any], keys: Iterable[str], default: str) -> str:
    for key in keys:
        value: Any = item.get(key)
        if value is not None and value != "":
            return str(value)
    return default


def _normalize_level(value: str) -> str:
    upper: str = value.upper()
    if upper == "WARN":
        return "WARNING"
    if upper in LEVELS:
        return upper
    return ""


def _infer_level(text: str) -> str:
    upper: str = text.upper()
    for level in ("CRITICAL", "FATAL", "ERROR", "WARNING", "WARN", "INFO", "DEBUG", "TRACE"):
        if re.search(rf"\b{level}\b", upper):
            return "CRITICAL" if level == "FATAL" else "WARNING" if level == "WARN" else level
    if any(token in upper for token in ("EXCEPTION", "FAILED", "FAILURE", "TIMEOUT", "UNAVAILABLE")):
        return "ERROR"
    return "INFO"


def _infer_error_type(text: str) -> str:
    lower: str = text.lower()
    if "timeout" in lower:
        return "timeout"
    if "connection" in lower or "pool" in lower:
        return "connection_error"
    if "memory" in lower or "heap" in lower:
        return "memory_pressure"
    return ""


def _extract_timestamp(text: str) -> str:
    for pattern in (ISO_TS_RE, SYSLOG_TS_RE, HDFS_TS_RE):
        match = pattern.search(text)
        if match:
            return match.group(1)
    return ""
