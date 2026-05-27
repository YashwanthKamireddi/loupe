"""Phase J — production hardening tests.

Covers four enterprise-readiness features shipped in v0.0.63:

  J1. Retention policy   — ``loupe purge --auto`` reads ``[retention]``.
  J2. Custom redaction   — user regexes in ``[redact.patterns]`` redact too.
  J3. Encryption at rest — opt-in JSONL envelope decrypts transparently.
  J6. Parquet export     — analytics-friendly columnar export.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from loupe import record_step, trace
from loupe.store import JSONLStore


@pytest.fixture()
def loupe_home(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    home = tmp_path / "loupe-home"
    home.mkdir()
    monkeypatch.setenv("LOUPE_HOME", str(home))
    monkeypatch.setenv("LOUPE_DISABLE_INDEX", "1")
    from loupe import store as store_mod
    store_mod._default = None
    # Force the redact cache to re-read the per-test config.toml
    from loupe import _redact
    _redact._reset_custom_pattern_cache()
    return home


# ===========================================================================
# J1 — Retention policy
# ===========================================================================


def test_retention_config_loads_defaults(loupe_home: Path) -> None:
    """Fresh install: retention = 0 (unlimited), keep_tagged = True."""
    from loupe.config import Config
    cfg = Config.load()
    assert cfg.retention_max_age_days == 0
    assert cfg.retention_keep_tagged is True


def test_retention_config_reads_explicit_values(loupe_home: Path) -> None:
    """A config.toml with [retention] gets surfaced on Config."""
    (loupe_home / "config.toml").write_text(
        "[retention]\nmax_age_days = 14\nkeep_tagged = false\n",
        encoding="utf-8",
    )
    from loupe.config import Config
    cfg = Config.load()
    assert cfg.retention_max_age_days == 14
    assert cfg.retention_keep_tagged is False


def test_retention_config_coerces_garbage_to_zero(loupe_home: Path) -> None:
    """A malformed [retention] block must NOT crash Config.load() —
    we coerce ``max_age_days = "nope"`` to 0 (= disabled)."""
    (loupe_home / "config.toml").write_text(
        '[retention]\nmax_age_days = "nope"\n',
        encoding="utf-8",
    )
    from loupe.config import Config
    cfg = Config.load()
    assert cfg.retention_max_age_days == 0


def test_purge_auto_reads_retention(
    loupe_home: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    """`loupe purge --auto` should pick up max_age_days from config.toml."""
    from typer.testing import CliRunner

    from loupe.cli import app

    (loupe_home / "config.toml").write_text(
        "[retention]\nmax_age_days = 7\nkeep_tagged = true\n",
        encoding="utf-8",
    )
    res = CliRunner().invoke(app, ["purge", "--auto"])
    # Either prints "no traces older than 7d" or "no traces to purge"
    # — both are acceptable on a freshly-seeded LOUPE_HOME.
    assert res.exit_code == 0
    assert ("no traces" in res.output) or ("would delete" in res.output)


def test_purge_auto_warns_when_retention_disabled(loupe_home: Path) -> None:
    """If retention.max_age_days = 0, --auto should explain + exit 0."""
    from typer.testing import CliRunner

    from loupe.cli import app

    res = CliRunner().invoke(app, ["purge", "--auto"])
    assert res.exit_code == 0
    assert "retention is OFF" in res.output


# ===========================================================================
# J2 — Custom redaction patterns
# ===========================================================================


def test_custom_redact_pattern_applied(loupe_home: Path) -> None:
    """A regex declared in [redact.patterns] should scrub matching values."""
    (loupe_home / "config.toml").write_text(
        '[redact]\npatterns = ["INTERNAL-[A-Z]{4}-\\\\d{4}"]\n',
        encoding="utf-8",
    )
    from loupe import _redact
    _redact._reset_custom_pattern_cache()
    payload = {"note": "see ticket INTERNAL-FROG-1234 for context"}
    out = _redact.redact(payload)
    assert "[redacted]" in out["note"]
    assert "INTERNAL-FROG-1234" not in out["note"]


def test_default_redactor_unchanged_without_user_config(loupe_home: Path) -> None:
    """No config.toml [redact] block → only the built-in patterns fire.

    Verifies the user-pattern hook didn't accidentally widen the default."""
    from loupe import _redact
    _redact._reset_custom_pattern_cache()
    out = _redact.redact({
        "ok":  "plain text — no secrets here",
        "bad": "Authorization: Bearer ya29.AbCdEfG_long_token_data",
    })
    # Bearer token always gets caught by the built-in patterns.
    assert "[redacted]" in out["bad"]
    # Innocent text passes through.
    assert out["ok"] == "plain text — no secrets here"


def test_invalid_regex_in_config_is_skipped(loupe_home: Path) -> None:
    """An unparseable regex must not crash the redactor."""
    (loupe_home / "config.toml").write_text(
        '[redact]\npatterns = ["(unbalanced"]\n',
        encoding="utf-8",
    )
    from loupe import _redact
    _redact._reset_custom_pattern_cache()
    # If the bad regex crashed, this would raise.
    out = _redact.redact({"x": "hello"})
    assert out == {"x": "hello"}


# ===========================================================================
# J3 — Encryption at rest
# ===========================================================================


def _enable_encryption(home: Path) -> None:
    (home / "config.toml").write_text(
        "[encryption]\nenabled = true\n",
        encoding="utf-8",
    )


def test_encryption_off_writes_plaintext_jsonl(loupe_home: Path) -> None:
    """With encryption off, files on disk are vanilla JSONL — no envelope."""
    store = JSONLStore(root=loupe_home / "traces")

    @trace(name="plain-agent", framework="test", store=store)
    def run() -> None:
        record_step("llm-call", "x")
    run()

    files = list((loupe_home / "traces").glob("*.jsonl"))
    assert len(files) == 1
    text = files[0].read_text(encoding="utf-8")
    assert text.startswith('{"_type":"trace"')
    assert "LOUPE-ENC-V1:" not in text


def test_encryption_on_writes_envelope_and_round_trips(loupe_home: Path) -> None:
    """With encryption on, the file is a single envelope line; reading
    via read_trace_text() decrypts to the original JSONL."""
    pytest.importorskip("cryptography")
    _enable_encryption(loupe_home)

    store = JSONLStore(root=loupe_home / "traces")

    @trace(name="encrypted-agent", framework="test", store=store)
    def run() -> None:
        record_step("llm-call", "anthropic:claude",
                    outputs={"text": "secret payload"})
    run()

    files = list((loupe_home / "traces").glob("*.jsonl"))
    assert len(files) == 1
    raw = files[0].read_text(encoding="utf-8")
    assert raw.startswith("LOUPE-ENC-V1:")
    # The cleartext name should NOT appear in the ciphertext
    assert "encrypted-agent" not in raw
    assert "secret payload" not in raw

    from loupe._crypto import read_trace_text
    decrypted = read_trace_text(files[0])
    assert '"_type":"trace"' in decrypted
    assert "encrypted-agent" in decrypted
    assert "secret payload" in decrypted


def test_encryption_key_file_is_0600(loupe_home: Path) -> None:
    """The key file must be owner-read-only after creation."""
    pytest.importorskip("cryptography")
    _enable_encryption(loupe_home)

    from loupe._crypto import get_key_path, get_or_create_key
    get_or_create_key(loupe_home)
    key_path = get_key_path(loupe_home)
    assert key_path.exists()
    mode = key_path.stat().st_mode & 0o777
    # Linux + macOS: should be exactly 0600. Windows: chmod is a no-op,
    # so we only assert the file isn't world-readable.
    assert (mode == 0o600) or ((mode & 0o077) == 0)


def test_read_trace_text_handles_plaintext_passthrough(loupe_home: Path) -> None:
    """Backward compatibility: read_trace_text() on a plain JSONL file
    must return its content verbatim, no decryption attempted."""
    trace_path = loupe_home / "traces" / "plain.jsonl"
    trace_path.parent.mkdir(parents=True, exist_ok=True)
    body = '{"_type":"trace","trace_id":"x","name":"x","framework":"x","started_at":0}\n'
    trace_path.write_text(body, encoding="utf-8")
    from loupe._crypto import read_trace_text
    assert read_trace_text(trace_path) == body


def test_encryption_failure_does_not_lose_trace(
    loupe_home: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    """If encryption raises mid-save, the store must fall back to writing
    plaintext rather than losing the trace silently."""
    _enable_encryption(loupe_home)

    # Sabotage the encryption helper to always raise.
    from loupe import _crypto

    def _boom(*_args, **_kw):
        raise RuntimeError("simulated crypto outage")
    monkeypatch.setattr(_crypto, "encrypt_payload", _boom)

    store = JSONLStore(root=loupe_home / "traces")

    @trace(name="resilient-agent", framework="test", store=store)
    def run() -> None:
        record_step("llm-call", "x")
    run()

    files = list((loupe_home / "traces").glob("*.jsonl"))
    assert len(files) == 1
    text = files[0].read_text(encoding="utf-8")
    # Wrote PLAIN JSONL despite encryption being requested.
    assert text.startswith('{"_type":"trace"')


# ===========================================================================
# J6 — Parquet export
# ===========================================================================


def test_parquet_export_writes_file_with_step_rows(loupe_home: Path) -> None:
    """`loupe export --format parquet` writes one row per step."""
    pytest.importorskip("duckdb")
    import duckdb

    # Seed two traces with 2 steps each.
    store = JSONLStore(root=loupe_home / "traces")
    for i in range(2):
        @trace(name=f"agent-{i}", framework="test", store=store)
        def run(idx: int = i) -> None:
            record_step("plan", "compose")
            record_step("llm-call", f"call-{idx}",
                        outputs={"text": "hi", "input_tokens": 3,
                                 "output_tokens": 1, "status": 200})
        run()

    from loupe._parquet import export_traces_to_parquet
    out = loupe_home / "out.parquet"
    trace_n, step_n, written = export_traces_to_parquet(
        traces_dir=loupe_home / "traces",
        out=out,
        trace_id_prefix=None,
    )
    assert trace_n == 2
    assert step_n == 4
    assert written == out
    assert out.exists()

    rows = duckdb.connect().execute(
        f"SELECT step_kind, count(*) c FROM '{out.as_posix()}' "
        "GROUP BY step_kind ORDER BY step_kind"
    ).fetchall()
    by_kind = dict(rows)
    assert by_kind.get("llm-call") == 2
    assert by_kind.get("plan") == 2


def test_parquet_export_empty_dir_emits_valid_empty_file(loupe_home: Path) -> None:
    """Even when there are zero rows, we still emit a Parquet file with a
    well-defined schema so downstream pipelines don't break."""
    pytest.importorskip("duckdb")
    import duckdb

    (loupe_home / "traces").mkdir(parents=True, exist_ok=True)
    from loupe._parquet import export_traces_to_parquet
    out = loupe_home / "empty.parquet"
    trace_n, step_n, _ = export_traces_to_parquet(
        traces_dir=loupe_home / "traces", out=out, trace_id_prefix=None,
    )
    assert trace_n == 0
    assert step_n == 0
    assert out.exists()
    # Schema-only read: column count > 0 so it's a real Parquet, not empty bytes.
    cols = duckdb.connect().execute(
        f"DESCRIBE SELECT * FROM '{out.as_posix()}'"
    ).fetchall()
    assert len(cols) >= 10  # we defined 24 columns


def test_parquet_export_respects_trace_id_prefix(loupe_home: Path) -> None:
    """``trace_id_prefix`` should limit the export to matching traces."""
    pytest.importorskip("duckdb")

    # Manually write two traces so we control the IDs.
    traces_dir = loupe_home / "traces"
    traces_dir.mkdir(parents=True, exist_ok=True)
    for tid in ("aaaa1111", "bbbb2222"):
        path = traces_dir / f"{tid}.jsonl"
        path.write_text(
            json.dumps({"_type": "trace", "trace_id": tid, "name": tid,
                        "framework": "t", "started_at": 1.0}) + "\n"
            + json.dumps({"_type": "step", "step_id": "s1", "kind": "thought",
                          "name": "x", "started_at": 1.0, "ended_at": 1.0,
                          "inputs": {}, "outputs": {}, "metadata": {}}) + "\n",
            encoding="utf-8",
        )

    from loupe._parquet import export_traces_to_parquet
    out = loupe_home / "filtered.parquet"
    trace_n, step_n, _ = export_traces_to_parquet(
        traces_dir=traces_dir, out=out, trace_id_prefix="aaaa",
    )
    assert trace_n == 1
    assert step_n == 1
