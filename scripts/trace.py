"""Print the provenance of one issuance, the same body as GET /admin/issuances/{reference}.

python scripts/trace.py <reference>      (needs DATABASE_URL)
TRACE_VALUE=<code> python scripts/trace.py "" <owner>   by owner and token value (also needs TOKEN_PEPPER); the value
is read from the environment, hashed, and never printed
"""

import json
import os
import sys
import uuid

from qrypt_entropy.errors import AppError
from qrypt_entropy.service import issuance, tokens


def main(argv: list[str]) -> int:
    try:
        if len(argv) > 2 and argv[2]:
            reference = tokens.find_issuance(argv[2], os.environ.get("TRACE_VALUE", ""))
        else:
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
