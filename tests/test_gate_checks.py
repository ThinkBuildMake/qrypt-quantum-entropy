import subprocess

from tools.check_imports import find_violations
from tools.check_language import find_foreign_sources
from tools.secret_scan import scan_text


def write(root, rel, text):
    path = root / "qrypt_entropy" / rel
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text)


def test_ac8_import_check_passes_on_the_repository():
    assert find_violations("src") == []


def test_ac8_import_check_flags_a_module_importing_a_transport(tmp_path):
    write(tmp_path, "service/pool.py", "from qrypt_entropy.transport import mcp_server\n")
    violations = find_violations(tmp_path)
    assert len(violations) == 1
    assert "service/pool.py" in violations[0]


def test_ac8_import_check_flags_upward_and_agent_storage_imports(tmp_path):
    write(tmp_path, "storage/db.py", "import qrypt_entropy.service.access\n")
    write(tmp_path, "upstream/source.py", "from qrypt_entropy.service import pool\n")
    write(tmp_path, "agent/chat.py", "from qrypt_entropy.storage import db\n")
    write(
        tmp_path,
        "service/ok.py",
        "from qrypt_entropy.upstream import source\nfrom qrypt_entropy import errors\n",
    )
    assert len(find_violations(tmp_path)) == 3


def test_ac8_repository_holds_one_source_language():
    assert find_foreign_sources(subprocess.check_output(["git", "ls-files"], text=True).split()) == []
    assert find_foreign_sources(["src/app.ts", "x.py", "main.go"]) == ["src/app.ts", "main.go"]


def test_ac8_secret_scan_flags_keys_and_passes_placeholders():
    fake_aws = "AKIA" + "ABCDEFGHIJKLMNOP"
    assert scan_text(f"aws = '{fake_aws}'", "x.py")
    assert scan_text("-----BEGIN RSA " + "PRIVATE KEY-----", "x.pem")
    assert scan_text("QRYPT_API_TOKEN=" + "a1b2c3d4e5f6g7h8i9j0k1l2", ".env")
    assert scan_text("QRYPT_API_TOKEN=", ".env.example") == []
    assert scan_text("MCP_CLIENT_KEY=stub-client-key", "docs/x.json") == []
