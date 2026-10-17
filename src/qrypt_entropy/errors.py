"""Stable error codes shared by every transport (ARCHITECTURE: System-wide contracts, Error format)."""

import json

HTTP_STATUS = {
    "invalid_argument": 400,
    "unauthorized": 401,
    "forbidden": 403,
    "not_found": 404,
    "budget_exhausted": 503,
    "temporarily_unavailable": 503,
    "upstream_misconfigured": 503,
    "database_unavailable": 503,
}


class AppError(Exception):
    """An anticipated failure. The message never contains a value."""

    code = "temporarily_unavailable"

    def __init__(self, message: str, code: str | None = None):
        super().__init__(message)
        self.code = code or type(self).code
        self.message = message

    @property
    def http_status(self) -> int:
        return HTTP_STATUS.get(self.code, 500)

    def body(self) -> dict[str, str]:
        return {"error": self.code, "message": self.message}

    def json(self) -> str:
        return json.dumps(self.body())


class InvalidArgument(AppError):
    code = "invalid_argument"


class Unauthorized(AppError):
    code = "unauthorized"


class Forbidden(AppError):
    code = "forbidden"


class NotFound(AppError):
    code = "not_found"


class BudgetExhausted(AppError):
    code = "budget_exhausted"


class TemporarilyUnavailable(AppError):
    code = "temporarily_unavailable"


class Misconfigured(AppError):
    """Upstream 401: the Qrypt token is invalid or expired (DE-5)."""

    code = "upstream_misconfigured"


class AllotmentUsed(BudgetExhausted):
    """Upstream 403: the Qrypt allotment is used (RL-2)."""


class Unavailable(TemporarilyUnavailable):
    """Upstream 5xx, timeout or connection failure (DE-4, RL-2)."""


class RateLimited(TemporarilyUnavailable):
    """Upstream 429 persisted after the backoff rules (DE-3)."""


class PoolStarved(AppError):
    """The pool cannot cover a call. The code is the caller-facing reason (ARCHITECTURE: Pool)."""

    def __init__(self, message: str, reason: str = "temporarily_unavailable"):
        super().__init__(message, reason)
        self.reason = reason
