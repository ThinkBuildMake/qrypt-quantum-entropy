"""`make report`: print the metrics panels."""

import sys

import psycopg

from qrypt_entropy.config import get_settings
from qrypt_entropy.service.report import build_panels


def main() -> int:
    with psycopg.connect(get_settings().database_url) as conn:
        panels = build_panels(conn)
    for number, panel in enumerate(panels, 1):
        print(f"PANEL {number} {panel.name} [{panel.source}]: {panel.reading}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
