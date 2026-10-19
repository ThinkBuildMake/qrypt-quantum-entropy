"""`make report`: print the metrics panels."""

import sys

from qrypt_entropy.service.report import build_panels
from qrypt_entropy.storage import db


def main() -> int:
    with db.connect() as conn:
        panels = build_panels(conn)
    for number, panel in enumerate(panels, 1):
        print(f"PANEL {number} {panel.name} [{panel.source}]: {panel.reading}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
