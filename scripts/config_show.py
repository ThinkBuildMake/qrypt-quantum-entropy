"""Print the effective settings, secrets redacted (AC7: no setting selects another source)."""

from pydantic import SecretStr

from qrypt_entropy.config import get_settings

for name, value in sorted(get_settings().model_dump().items()):
    secret = isinstance(value, SecretStr) or any(word in name for word in ("token", "key", "secret", "password", "url"))
    print(f"{name}={'<set>' if secret and value else value}")
