"""Print the provenance of one issuance, the same body as GET /admin/issuances/{reference}.

python scripts/trace.py <reference>      (needs DATABASE_URL)
"""

import json
import sys
import uuid

from qrypt_entropy.errors import AppError
from qrypt_entropy.service import issuance


def main(argv: list[str]) -> int:
    try:
        reference = str(uuid.UUID(argv[1] if len(argv) > 1 else ""))
        print(json.dumps(issuance.get_issuance(reference), indent=2))
    except ValueError:
        print('{"error": "invalid_argument", "message": "reference must be a uuid"}', file=sys.stderr)
        return 2
    except AppError as err:
        print(err.json(), file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
