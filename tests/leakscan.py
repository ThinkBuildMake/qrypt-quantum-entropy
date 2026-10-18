"""The leak-scan toolkit (S38): known markers held in memory, searched for in raw, hex and base64 form.

A `Marker` knows its own forms. `Finder` searches a corpus (bytes) and reports only labels and form names, never a
marker's value, so a failing scan cannot itself leak. Nothing here writes a marker to disk or prints one.
"""

import base64
import re
import uuid
from dataclasses import dataclass, field

SKIP_DIRS = {".git", ".venv", "__pycache__", ".pytest_cache", ".ruff_cache", "node_modules"}


@dataclass
class Marker:
    label: str
    forms: dict[str, bytes] = field(repr=False)  # form name -> bytes searched for
    short: bool = False  # a 6-digit code: only searched where context rules out coincidence

    @classmethod
    def of(cls, label: str, value: str | bytes) -> Marker:
        """A text secret or value, or raw bytes: raw, hex (both cases) and base64 (padded, unpadded, URL-safe)."""
        raw = value.encode() if isinstance(value, str) else value
        forms = {"raw": raw}
        forms["hex"] = raw.hex().encode()
        forms["HEX"] = raw.hex().upper().encode()
        std, url = base64.b64encode(raw), base64.urlsafe_b64encode(raw)
        forms |= {"base64": std, "base64-unpadded": std.rstrip(b"="), "base64url": url.rstrip(b"=")}
        if isinstance(value, str):
            try:
                # a hex-text value (an API key) is also searched as the bytes it encodes
                forms["decoded-hex"] = bytes.fromhex(value) if len(value) >= 16 else b""
            except ValueError:
                pass
            try:
                forms["decoded-uuid"] = uuid.UUID(value).bytes
                forms["uuid-no-dashes"] = uuid.UUID(value).hex.encode()
            except ValueError:
                pass
        forms = {name: f for name, f in forms.items() if len(f) >= 8 or (name == "raw" and f)}
        short = isinstance(value, str) and value.isdigit() and len(value) < 8
        return cls(label, forms, short)


class Finder:
    def __init__(self, markers: list[Marker]):
        self.markers = markers
        self._patterns = {
            (m.label, name): self._pattern(form, m.short and name == "raw")
            for m in markers
            for name, form in m.forms.items()
        }

    @staticmethod
    def _pattern(form: bytes, bounded: bool) -> re.Pattern:
        if not bounded:
            return re.compile(re.escape(form))
        # a short code counts only when it stands alone: not inside a longer number, name or timestamp fraction
        return re.compile(rb"(?<![0-9A-Za-z_])(?<!\d\.)" + re.escape(form) + rb"(?![0-9A-Za-z_])(?!\.\d)")

    def search(self, data: bytes, *, include_short: bool = True) -> set[tuple[str, str]]:
        """(label, form) of every marker found in `data`."""
        found = set()
        for m in self.markers:
            if m.short and not include_short:
                continue
            for name in m.forms:
                pattern = self._patterns[(m.label, name)]
                if pattern.search(data):
                    found.add((m.label, name))
        return found

    def search_text(self, text: str, **kw) -> set[tuple[str, str]]:
        return self.search(text.encode("utf-8", "surrogatepass"), **kw)
