"""Report panels fed from log files and status snapshots (S19). Pure readers: no database, no values.

Inputs, by environment variable (set by `make report LOG=... SNAPSHOTS=...`):
  LOG        JSON-lines log. Events read: `tool_call` (duration_ms), `reply_check` (found, backed),
             `pool_cannot_cover` and `unavailable_returned` (OB-8). Other lines are ignored.
  SNAPSHOTS  one MetricsSnapshot per JSON line (or a JSON array), each with `timestamp`,
             `startedAt`, `errorsByCode` and `upstreamRequestsLast10s`; written by the metrics sampler.
"""

import json
import math
import os
from collections import Counter, defaultdict
from datetime import UTC, datetime, timedelta
from pathlib import Path

NO_DATA = "no data"
P95_LIMIT_MS = 50
QRYPT_REQUESTS_LIMIT = 30
UNAVAILABLE_LIMIT_S = 2.0
WINDOW = timedelta(seconds=30)
CALLS_FOR_P95 = 1000
NOT_SERVER_CAUSED = {"invalid_argument", "unauthorized"}


def _lines(var: str) -> list[dict]:
    """Parsed JSON objects of the file named by env var `var`; [] when unset, missing or unreadable."""
    path = os.environ.get(var)
    if not path or not Path(path).is_file():
        return []
    text = Path(path).read_text(encoding="utf-8", errors="replace")
    try:
        whole = json.loads(text)
        return [r for r in (whole if isinstance(whole, list) else [whole]) if isinstance(r, dict)]
    except ValueError:
        pass
    records = []
    for line in text.splitlines():
        try:
            record = json.loads(line)
        except ValueError:
            continue
        if isinstance(record, dict):
            records.append(record)
    return records


def _time(record: dict) -> datetime | None:
    try:
        moment = datetime.fromisoformat(str(record["timestamp"]))
    except KeyError, ValueError:
        return None
    return moment if moment.tzinfo else moment.replace(tzinfo=UTC)


def _events(name: str) -> list[dict]:
    return [r for r in _lines("LOG") if r.get("event") == name]


def _number(value) -> float | None:
    return float(value) if isinstance(value, int | float) and not isinstance(value, bool) else None


def _snapshots() -> list[tuple[datetime, dict]]:
    stamped = [(t, r) for r in _lines("SNAPSHOTS") if (t := _time(r))]
    return sorted(stamped, key=lambda pair: pair[0])


def tool_call_p95(conn=None) -> str:
    durations = [d for r in _events("tool_call") if (d := _number(r.get("duration_ms"))) is not None]
    durations = durations[-CALLS_FOR_P95:]
    if not durations:
        return NO_DATA
    durations.sort()
    p95 = durations[math.ceil(0.95 * len(durations)) - 1]  # nearest rank
    verdict = "above" if p95 > P95_LIMIT_MS else "within"
    return f"{p95:g} ms over {len(durations)} calls ({verdict} the {P95_LIMIT_MS} ms threshold)"


def _delta(end: dict, start: dict) -> float:
    """Server-caused errors between two reports; a new `startedAt` or a lower counter means a restart."""
    restarted = end.get("startedAt") != start.get("startedAt")
    total = 0.0
    for code, value in (end.get("errorsByCode") or {}).items():
        now, before = _number(value) or 0, _number((start.get("errorsByCode") or {}).get(code)) or 0
        if code not in NOT_SERVER_CAUSED:
            total += now - before if now >= before and not restarted else now
    return total


def server_caused_errors(conn=None) -> str:
    snaps = _snapshots()
    if len(snaps) < 2:
        return NO_DATA
    windows = []
    for end_time, end in snaps:
        starts = [s for t, s in snaps if t <= end_time - WINDOW]
        if starts:
            windows.append(_delta(end, starts[-1]))
    if not windows:  # less than 30 s of history: first to last report is the one window
        windows.append(_delta(snaps[-1][1], snaps[0][1]))
    worst = max(windows)
    verdict = "within the limit of 0" if worst == 0 else "above the limit of 0"
    return f"{worst:g} in the worst 30 s window ({verdict})"


def _day(moment: datetime) -> str:
    return moment.astimezone(UTC).date().isoformat()


def _verdict(value: float, limit: float) -> str:
    return "within" if value <= limit else "above"


def worst_10_seconds(conn=None) -> str:
    worst: dict[str, float] = defaultdict(float)
    for moment, snapshot in _snapshots():
        if (v := _number(snapshot.get("upstreamRequestsLast10s"))) is not None:
            worst[_day(moment)] = max(worst[_day(moment)], v)
    if not worst:
        return NO_DATA
    return "; ".join(
        f"{day}: {v:g} requests ({_verdict(v, QRYPT_REQUESTS_LIMIT)} the limit of {QRYPT_REQUESTS_LIMIT})"
        for day, v in sorted(worst.items())
    )


def unbacked_replies(conn=None) -> str:
    replies: Counter = Counter()
    unbacked: Counter = Counter()
    for r in _events("reply_check"):
        found, backed, moment = _number(r.get("found")), _number(r.get("backed")), _time(r)
        if found is None or backed is None or moment is None:
            continue
        day = _day(moment)
        replies[day] += 1
        unbacked[day] += backed < found
    if not replies:
        return NO_DATA
    return "; ".join(f"{day}: {unbacked[day]} of {replies[day]} replies" for day in sorted(replies))


def time_to_unavailable(conn=None) -> str:
    """Per day, the worst `waited_s` of the pool's error-returned events (each is one outage's wait)."""
    waits: dict[str, list[float]] = defaultdict(list)
    for r in _events("pool_error_returned"):
        moment, waited = _time(r), _number(r.get("waited_s"))
        if moment is not None and waited is not None:
            waits[_day(moment)].append(waited)
    if not waits:
        return NO_DATA
    return "; ".join(
        f"{day}: {max(w):.1f} s worst of {len(w)} ({_verdict(max(w), UNAVAILABLE_LIMIT_S)} the "
        f"{UNAVAILABLE_LIMIT_S:g} s limit)"
        for day, w in sorted(waits.items())
    )
