import importlib
import pkgutil

import qrypt_entropy
from qrypt_entropy.agent.chat import chat_turn
from qrypt_entropy.stubs import STUBS


def test_every_registered_stub_is_listed_in_docs_stubs_with_its_replacing_story():
    for info in pkgutil.walk_packages(qrypt_entropy.__path__, "qrypt_entropy."):
        importlib.import_module(info.name)
    doc = open("docs/STUBS.md").read()
    assert len(STUBS) >= 20
    for stub in STUBS:
        row = next((ln for ln in doc.splitlines() if f"`{stub.qualname}`" in ln and stub.module in ln), None)
        assert row is not None, f"{stub.module}.{stub.qualname} missing from docs/STUBS.md"
        assert stub.replaced_by in row, f"{stub.qualname}: wrong story in docs/STUBS.md"


def test_agent_stub_says_it_issued_nothing():
    assert "no value was issued" in chat_turn("issue a code")
