"""The reply check (D-17, OB-7, EV-2): before a reply reaches the user, every value-shaped string in it must be one
the tools returned in this conversation. Anything else is removed, the user is told, and one `reply_check` event
records how many values were found and how many were backed, never the values.

The scan is a heuristic, not a proof (architecture open question 6). It finds a UUID, a run of 32 or more hex
digits, a run of 6 or more digits, and a 20+ character base64-like string holding a letter and a digit, each not
touching more of its own kind, so "OTP-731905" and "code=731905" are caught. The ids a tool returned as metadata
(batch ids, reference) count as backed. It does not find: a short number (a random_int of 1 to 5 digits), a value
split by spaces, commas or dashes ("731 905"), spelled out in words, reversed or encoded, one run into letters
("code731905x"), or a value hidden in an image or link. It cannot tell an invented number from an ordinary long one
(a phone number, a timestamp): those are removed too, which errs on the safe side. A model that copies a returned
value it saw in this conversation counts as backed.
"""

import re

from qrypt_entropy.logs import get_logger

log = get_logger("agent")

_UUID = r"(?<![0-9a-fA-F])[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{12}(?![0-9a-fA-F])"
_HEX = r"(?<![0-9a-fA-F])[0-9a-fA-F]{32,}(?![0-9a-fA-F])"
_BASE64 = r"(?<![\w+/=-])(?=[A-Za-z0-9+/_=-]*\d)(?=[A-Za-z0-9+/_=-]*[A-Za-z])[A-Za-z0-9+/_-]{20,}={0,2}(?![\w+/=-])"
_DIGITS = r"(?<!\d)\d{6,}(?!\d)"
VALUE = re.compile(f"{_UUID}|{_HEX}|{_BASE64}|{_DIGITS}")
REMOVED = "[removed]"
NOTICE = (
    "I removed a code or number from my answer because no tool issued it, so no value was issued for it. "
    "Ask me and I will issue a real one."
)


def check_reply(reply: str, issued: dict[str, str], provenance: set[str] = frozenset()) -> str:
    """Return the reply with unbacked values removed (and the notice added); log one event. `provenance` holds the
    ids the tools returned as metadata (batch ids, references), which the reply may quote."""
    backed_values = {value.lower() for value in (*issued.values(), *provenance)}
    found = backed = 0

    def judge(match: re.Match) -> str:
        nonlocal found, backed
        found += 1
        if match.group().lower() in backed_values:
            backed += 1
            return match.group()
        return REMOVED

    checked = VALUE.sub(judge, reply)
    log.info("reply_check", found=found, backed=backed)
    return checked if backed == found else f"{checked}\n\n{NOTICE}"
