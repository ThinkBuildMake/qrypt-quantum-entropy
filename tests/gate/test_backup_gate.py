"""S31 gate cases: backup-decrypt, backup-no-secrets, backup-workflow (plus the encrypted-only file, the restore
drill and the read-only backup role). A throwaway age key pair stands in for the real one, which stays offline."""

import os
import re
import subprocess
import sys
from pathlib import Path

import psycopg
import pytest
from psycopg import errors, sql
from sqlalchemy.engine import make_url

ROOT = Path(__file__).resolve().parents[2]
PEPPER = "test-pepper-not-a-secret"
HASH_SECRET = "placeholder-hash-secret-for-tests"
BACKUP_PASSWORD = "backup-test-only"  # the role is cluster-wide: same value as tests/test_dbpolicy.py
ISSUANCE, BATCH = "00000000-0000-4000-8000-0000000000b1", "00000000-0000-4000-8000-0000000000b2"
OUTCOME_SQL = (
    "INSERT INTO redemption_outcomes (id, owner, outcome) VALUES (gen_random_uuid(), 'seed-owner-1', 'unknown')"
)
WORKFLOW = ROOT / ".github/workflows/backup.yml"


def run(cmd, env=None, **kw):
    env = {
        k: v for k, v in (env or os.environ).items() if k != "UV"
    }  # uv sets UV to its path, Makefile wants a command
    return subprocess.run(cmd, cwd=ROOT, env=env, capture_output=True, text=True, timeout=120, **kw)


def keypair(path: Path) -> str:
    """Make an age key pair at `path` (private) and return its public key."""
    assert run(["age-keygen", "-o", str(path)]).returncode == 0
    return run(["age-keygen", "-y", str(path)]).stdout.strip()


@pytest.fixture
def filled(db):
    """A migrated database filled with synthetic rows: seeded credentials and tokens plus one row in every table.
    Returns the database URL and the plaintext secrets that must never reach a dump."""
    with psycopg.connect(db, autocommit=True) as conn:
        conn.execute(sql.SQL("ALTER ROLE backup PASSWORD {}").format(sql.Literal(BACKUP_PASSWORD)))
    env = {**os.environ, "DATABASE_URL": db, "TOKEN_PEPPER": PEPPER, "KEY_HASH_SECRET": HASH_SECRET}
    seeded = run([sys.executable, "scripts/seed.py"], env=env)
    assert seeded.returncode == 0, seeded.stderr
    keys = re.findall(r"^credential .* key=(\S+)$", seeded.stdout, re.M)
    values = re.findall(r"^seed_token .* value=(\S+)$", seeded.stdout, re.M)
    assert len(keys) == 3 and len(values) == 3
    with psycopg.connect(db, autocommit=True) as conn:
        client = conn.execute("SELECT id FROM credentials WHERE role = 'client' LIMIT 1").fetchone()[0]
        conn.execute(
            "INSERT INTO issuances (id, client_id, tool, bytes_consumed) VALUES (%s, %s, 'random_int', 1)",
            (ISSUANCE, client),
        )
        conn.execute("INSERT INTO batches (id, purpose, kib) VALUES (%s, 'refill', 1)", (BATCH,))
        conn.execute("INSERT INTO issuance_batches VALUES (%s, %s, 0, 1)", (ISSUANCE, BATCH))
        conn.execute(OUTCOME_SQL)
        conn.execute(
            "INSERT INTO server_status (instance_id, pool_level_bytes, low_mark_bytes, high_mark_bytes, "
            "upstream_status, snapshot) VALUES (gen_random_uuid(), 0, 1, 2, 'ok', '{}')"
        )
    return db, [PEPPER, HASH_SECRET, *keys, *values]


def backup_env(db: str, public_key: str) -> dict:
    url = make_url(db).set(username="backup", password=BACKUP_PASSWORD).render_as_string(hide_password=False)
    return {**os.environ, "BACKUP_URL": url, "BACKUP_AGE_RECIPIENT": public_key}


@pytest.fixture
def made(filled, tmp_path):
    """`make backup` has run against the filled database with the test public key."""
    db, secrets = filled
    identity = tmp_path / "identity.txt"
    env = backup_env(db, keypair(identity))
    out = tmp_path / "out"
    done = run(["make", "backup", f"BACKUP_DIR={out}"], env=env)
    assert done.returncode == 0, done.stderr
    return {"db": db, "secrets": secrets, "identity": identity, "out": out, "env": env, "tmp": tmp_path}


def decrypt(file: Path, identity: Path) -> subprocess.CompletedProcess:
    return subprocess.run(["age", "-d", "-i", str(identity), str(file)], capture_output=True, timeout=60)


def test_backup_leaves_one_encrypted_file_and_no_plain_dump(made):
    files = sorted(made["out"].iterdir())
    assert [f.name.endswith(".dump.age") for f in files] == [True]
    assert not files[0].read_bytes().startswith(b"PGDMP")  # a plain custom-format dump starts with this
    assert not list(made["tmp"].rglob("*.sql")) and not list(made["tmp"].rglob("*.dump"))


def test_backup_decrypt(made, tmp_path):
    (file,) = made["out"].iterdir()
    right = decrypt(file, made["identity"])
    assert right.returncode == 0 and right.stdout.startswith(b"PGDMP")
    listed = subprocess.run(["pg_restore", "-l"], input=right.stdout, capture_output=True, timeout=60)
    assert listed.returncode == 0 and b"TABLE DATA public tokens" in listed.stdout
    other = tmp_path / "other.txt"
    keypair(other)
    wrong = decrypt(file, other)
    assert wrong.returncode != 0 and wrong.stdout == b""


def test_backup_no_secrets(made):
    (file,) = made["out"].iterdir()
    dump = decrypt(file, made["identity"]).stdout
    text = subprocess.run(["pg_restore", "-f", "-"], input=dump, capture_output=True, timeout=60).stdout.decode()
    assert "billing-agent" in text and "seed-owner-1" in text  # the search can see data
    with psycopg.connect(made["db"]) as conn:  # control: a bytea column is printed in hex, and the search sees it
        assert bytes(conn.execute("SELECT key_hash FROM credentials LIMIT 1").fetchone()[0]).hex() in text
    found = [s for s in made["secrets"] if s in text or s.encode().hex() in text]
    assert found == []


def test_backup_failure_leaves_no_file(filled, tmp_path):
    db, _ = filled
    env = backup_env(db, keypair(tmp_path / "id.txt"))
    out = tmp_path / "out"
    bad = run(["make", "backup", f"BACKUP_DIR={out}"], env={**env, "BACKUP_URL": env["BACKUP_URL"] + "x"})
    assert bad.returncode != 0 and BACKUP_PASSWORD not in bad.stdout + bad.stderr
    assert list(out.iterdir()) == []
    no_key = run(
        ["make", "backup", f"BACKUP_DIR={out}"], env={k: v for k, v in env.items() if k != "BACKUP_AGE_RECIPIENT"}
    )
    assert no_key.returncode != 0 and list(out.iterdir()) == []


def test_backup_role_can_read_but_not_write(filled):
    db, _ = filled
    url = backup_env(db, "")["BACKUP_URL"]
    with psycopg.connect(url, autocommit=True) as conn:
        assert conn.execute("SELECT count(*) FROM tokens").fetchone()[0] == 3
        with pytest.raises(errors.InsufficientPrivilege):
            conn.execute("INSERT INTO tokens (id) VALUES (gen_random_uuid())")


def drill_env(made, **extra):
    return {**os.environ, "AGE_IDENTITY": str(made["identity"]), "BACKUP_FILE": str(next(made["out"].iterdir())),
            "SOURCE_URL": made["db"], **extra}  # fmt: skip


def test_restore_drill_brings_back_every_table_and_the_roles(made):
    done = run(["make", "restore-drill"], env=drill_env(made))
    assert done.returncode == 0, done.stdout + done.stderr
    assert "restore drill passed" in done.stdout
    assert "table tokens: original 3, restored 3" in done.stdout
    assert "roles present: ['backup', 'http_service', 'mcp_server', 'migrator']" in done.stdout


def test_restore_drill_fails_when_a_row_is_missing_from_the_backup(made):
    with psycopg.connect(made["db"], autocommit=True) as conn:  # written after the backup was taken
        conn.execute(OUTCOME_SQL)
    done = run(["make", "restore-drill"], env=drill_env(made))
    assert done.returncode != 0 and "row count of redemption_outcomes differs" in done.stdout


def test_restore_drill_migrates_to_the_dumps_revision_first_and_then_to_head(empty_db, tmp_path):
    env = {**os.environ, "DATABASE_URL": empty_db}
    assert run([sys.executable, "-m", "alembic", "upgrade", "s02_dbpolicy"], env=env).returncode == 0
    identity = tmp_path / "identity.txt"
    out = tmp_path / "out"
    admin_env = {
        **backup_env(empty_db, keypair(identity)),
        "BACKUP_URL": empty_db,
    }  # old schema: backup lacks the grant
    assert run(["make", "backup", f"BACKUP_DIR={out}"], env=admin_env).returncode == 0
    done = run(
        ["make", "restore-drill"], env={**env, "AGE_IDENTITY": str(identity), "BACKUP_FILE": str(next(out.iterdir()))}
    )
    assert done.returncode == 0, done.stdout + done.stderr
    assert (
        "backup holds schema revision s02_dbpolicy" in done.stdout
        and "table alembic_version: original 1, restored 1" in done.stdout
    )


def test_backup_passes_libpq_options_on_and_refuses_unknown_ones(filled, tmp_path):
    db, _ = filled
    env = backup_env(db, keypair(tmp_path / "id.txt"))
    ok = run(
        ["make", "backup", f"BACKUP_DIR={tmp_path / 'ok'}"],
        env={**env, "BACKUP_URL": env["BACKUP_URL"] + "?channel_binding=disable&target_session_attrs=any"},
    )
    assert ok.returncode == 0, ok.stderr
    bad = run(
        ["make", "backup", f"BACKUP_DIR={tmp_path / 'bad'}"],
        env={**env, "BACKUP_URL": env["BACKUP_URL"] + "?gssencmode=disable"},
    )
    assert bad.returncode != 0 and "gssencmode" in bad.stderr and not list((tmp_path / "bad").iterdir())


def test_backup_workflow():
    text = WORKFLOW.read_text()
    (cron,) = re.findall(r"^\s*-\s*cron:\s*['\"]([^'\"]+)['\"]", text, re.M)
    minute, hour, day, month, weekday = cron.split()
    assert minute.isdigit() and hour.isdigit() and (day, month, weekday) == ("*", "*", "*")  # once a day
    assert "actions/upload-artifact@" in text and "backups/*.dump.age" in text
    assert "BACKUP_URL: ${{ secrets.BACKUP_URL }}" in text
    assert not re.search(r"\becho\b|\bcat\b|\bprintenv\b|set -[a-z]*[xv]|^\s*env\s*$|--verbose", text, re.M)
    assert "ACTIONS_STEP_DEBUG" not in text
