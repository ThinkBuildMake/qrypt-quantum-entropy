"""`.env.example` lists every setting the processes read, with placeholders only (CP-1). Exit 1 listing problems."""

import re
import sys
from pathlib import Path

from pydantic import AliasChoices, SecretStr

from qrypt_entropy.config import Settings
from tools.secret_scan import scan_text

SECRET_SUFFIXES = ("key", "token", "pepper", "secret", "password")
URL_CREDENTIALS = re.compile(r"://([^:/@\s]+):([^@\s]*)@")


def env_names(field) -> set[str]:
    alias = field.validation_alias
    return {a.upper() for a in alias.choices} if isinstance(alias, AliasChoices) else {field.alias or ""}


def listed(text: str) -> dict[str, str]:
    pairs = (line.split("#")[0].partition("=") for line in text.splitlines() if "=" in line)
    return {name.strip(): value.strip() for name, _, value in pairs if name.strip() and not name.startswith("#")}


def problems(text: str | None = None) -> list[str]:
    text = Path(".env.example").read_text() if text is None else text
    values, found = listed(text), []
    for name, field in Settings.model_fields.items():
        names = env_names(field) - {""} or {name.upper()}
        present = names & set(values)
        if not present:
            found.append(f"{'/'.join(sorted(names))} is not listed in .env.example")
            continue
        var = present.pop()
        secret = field.annotation is not None and SecretStr in getattr(field.annotation, "__args__", ())
        if (secret or name.endswith(SECRET_SUFFIXES)) and values[var]:
            found.append(f"{var} must be empty in .env.example")
        match = URL_CREDENTIALS.search(values[var])
        if match and (match.group(1), match.group(2)) != ("USER", "PASSWORD"):
            found.append(f"{var} must use USER:PASSWORD as placeholder credentials")
    return found + scan_text(text, ".env.example")


if __name__ == "__main__":
    issues = problems()
    print("\n".join(issues) if issues else ".env.example lists every setting with placeholders only")
    sys.exit(1 if issues else 0)
