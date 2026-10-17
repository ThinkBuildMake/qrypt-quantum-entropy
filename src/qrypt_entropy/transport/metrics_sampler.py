"""`make sample-metrics`: scrape /metrics every few seconds and append one snapshot per scrape to a JSON-lines file.

Each line: {"timestamp", "errorsByCode", "callsByTool", "upstreamRequestsLast10s", "series"} where `series` holds
every scraped sample (name{labels} -> value). The report reads this file (`make report SNAPSHOTS=...`).
Series names read (S30 must export exactly these): qrypt_errors_total{code}, qrypt_calls_total{tool},
qrypt_upstream_requests_last_10s, qrypt_process_start_time_seconds (-> startedAt; a change means a restart).
Nothing here handles a value or a secret except the
admin key, which is only sent in the request header.
"""

import argparse
import json
import os
import sys
import time
from datetime import UTC, datetime

import httpx
from prometheus_client.parser import text_string_to_metric_families


def to_snapshot(text: str, now: datetime) -> dict:
    snap: dict = {"timestamp": now.isoformat(), "startedAt": None, "errorsByCode": {}, "callsByTool": {}, "series": {}}
    for family in text_string_to_metric_families(text):
        for s in family.samples:
            if s.name.endswith(("_created", "_bucket")):
                continue
            labels = ",".join(f"{k}={v}" for k, v in sorted(s.labels.items()))
            snap["series"][f"{s.name}{{{labels}}}" if labels else s.name] = s.value
            if s.name == "qrypt_errors_total" and "code" in s.labels:
                snap["errorsByCode"][s.labels["code"]] = s.value
            elif s.name == "qrypt_calls_total" and "tool" in s.labels:
                snap["callsByTool"][s.labels["tool"]] = s.value
            elif s.name == "qrypt_process_start_time_seconds":
                snap["startedAt"] = s.value
            elif s.name == "qrypt_upstream_requests_last_10s":
                snap["upstreamRequestsLast10s"] = s.value
    return snap


def sample(url: str, key: str | None, seconds: float, interval: float, out: str) -> int:
    headers = {"Authorization": f"Bearer {key}"} if key else {}
    deadline, taken = time.monotonic() + seconds, 0
    with httpx.Client(timeout=5.0, headers=headers) as client, open(out, "a", encoding="utf-8") as sink:
        while True:
            try:
                response = client.get(url)
                response.raise_for_status()
                sink.write(json.dumps(to_snapshot(response.text, datetime.now(UTC))) + "\n")
                sink.flush()
                taken += 1
            except (httpx.HTTPError, ValueError) as err:
                print(f"scrape failed: {type(err).__name__}", file=sys.stderr)
            if time.monotonic() + interval > deadline:
                return taken
            time.sleep(interval)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--url", default=os.environ.get("METRICS_URL", "http://localhost:8000/metrics"))
    parser.add_argument("--seconds", type=float, default=float(os.environ.get("SECONDS", 60)))
    parser.add_argument("--interval", type=float, default=float(os.environ.get("INTERVAL", 5)))
    parser.add_argument("--out", default=os.environ.get("SNAPSHOTS", "snaps.json"))
    args = parser.parse_args()
    taken = sample(args.url, os.environ.get("ADMIN_KEY"), args.seconds, args.interval, args.out)
    print(f"{taken} snapshots appended to {args.out}")
    return 0 if taken else 1


if __name__ == "__main__":
    sys.exit(main())
