"""Optional encryption-at-rest for captured Loupe traces.

Workflow when ``[encryption] enabled = true`` in ``~/.loupe/config.toml``:

  1. On first use, ``~/.loupe/.key`` is created with mode 0600 — a
     freshly-generated Fernet key (URL-safe base64-encoded 32 bytes).
  2. ``JSONLStore.save()`` calls :func:`encrypt_payload(text)` and writes
     a single envelope line ``LOUPE-ENC-V1:<ciphertext>\\n`` to disk.
     The file extension stays ``.jsonl`` so the rest of the toolchain
     finds it unchanged.
  3. Anything that reads a trace (``loupe show``, the dashboard, OTLP
     export, …) goes through :func:`read_trace_text(path)` instead of
     ``path.read_text()``. That helper detects the envelope and
     decrypts transparently.

Design choices:

- **Per-user key, per-machine.** No KMS, no passphrase prompts. The
  threat model is laptop / VM disk theft, not active adversaries with
  root. Sites with stricter requirements should layer dm-crypt /
  FileVault / BitLocker on top.
- **Envelope header.** ``LOUPE-ENC-V1:`` marks ciphertext explicitly so
  a corrupt or unencrypted file is never silently mis-read. New
  versions of the format can change the suffix.
- **Backward compatible.** Existing plaintext JSONL files are still
  readable. New writes are encrypted only when the user opted in.

The ``cryptography`` package is a transitive dep of fastapi + most LLM
SDKs, so this module is import-safe in 99% of installs. We still guard
the import so ``loupe`` itself never crashes on missing deps.
"""

from __future__ import annotations

import os
from pathlib import Path
from typing import Any

_ENVELOPE_PREFIX = "LOUPE-ENC-V1:"


class EncryptionUnavailableError(RuntimeError):
    """Raised when encryption is requested but ``cryptography`` is missing."""


# Backwards-compatible alias for callers that imported the old name.
EncryptionUnavailable = EncryptionUnavailableError


def _try_fernet() -> Any:
    """Return ``cryptography.fernet.Fernet`` or None.

    Lazy import: encrypting users pay the cost; everyone else doesn't.
    """
    try:
        from cryptography.fernet import Fernet
        return Fernet
    except ImportError:
        return None


def get_key_path(home: Path | None = None) -> Path:
    """Where the per-machine encryption key lives.

    Sits next to the config file. Stored outside ``traces/`` so a
    careless ``rm -rf ~/.loupe/traces`` doesn't lose the key.
    """
    if home is None:
        from loupe.store import _default_dir
        home = _default_dir()
    return home / ".key"


def get_or_create_key(home: Path | None = None) -> bytes:
    """Return the active encryption key, creating one on first use.

    File permissions are forced to 0600 even if the user re-created it
    by hand — a world-readable key would defeat the whole point.
    """
    Fernet = _try_fernet()  # noqa: N806 — matches cryptography class name
    if Fernet is None:
        raise EncryptionUnavailable(
            "encryption is enabled in config.toml but the `cryptography` "
            "package isn't installed. install it with `pip install cryptography` "
            "or set [encryption] enabled = false to disable."
        )
    path = get_key_path(home)
    path.parent.mkdir(parents=True, exist_ok=True)
    if path.exists():
        key = path.read_bytes().strip()
        if not key:
            # corrupt empty file — regenerate
            key = Fernet.generate_key()
            path.write_bytes(key)
    else:
        key = Fernet.generate_key()
        path.write_bytes(key)
    # Tighten permissions every time — `chmod 600` is a no-op on Windows
    # but on Linux + macOS it prevents accidental world-read.
    import contextlib
    with contextlib.suppress(OSError):
        os.chmod(path, 0o600)
    return key


def encryption_enabled() -> bool:
    """True iff ``[encryption] enabled = true`` AND cryptography is importable.

    Failure modes here MUST default to False so a broken config never
    silently swallows captured traces. The CLI's ``loupe doctor`` will
    surface explicit error states.
    """
    try:
        from loupe.config import Config
        cfg = Config.load()
        if not cfg.encryption_enabled:
            return False
    except Exception:
        return False
    return _try_fernet() is not None


def encrypt_payload(text: str, *, home: Path | None = None) -> str:
    """Encrypt a JSONL document into the envelope format.

    Returns the envelope line (no trailing newline) ready to write
    verbatim to ``<trace_id>.jsonl``.
    """
    Fernet = _try_fernet()  # noqa: N806 — matches cryptography class name
    if Fernet is None:
        raise EncryptionUnavailable(
            "cannot encrypt: `cryptography` package not installed"
        )
    key = get_or_create_key(home)
    token = Fernet(key).encrypt(text.encode("utf-8"))
    return f"{_ENVELOPE_PREFIX}{token.decode('ascii')}"


def decrypt_payload(envelope: str, *, home: Path | None = None) -> str:
    """Decrypt a single envelope line. Strips the prefix.

    Raises ``EncryptionUnavailable`` if cryptography is missing OR the
    key file is gone — both indicate the user's environment changed and
    the trace can't be read without intervention.
    """
    if not envelope.startswith(_ENVELOPE_PREFIX):
        raise ValueError("not a Loupe-encrypted payload")
    Fernet = _try_fernet()  # noqa: N806 — matches cryptography class name
    if Fernet is None:
        raise EncryptionUnavailable(
            "this trace is encrypted but `cryptography` is not installed"
        )
    key = get_or_create_key(home)
    token = envelope[len(_ENVELOPE_PREFIX):].encode("ascii")
    plaintext = Fernet(key).decrypt(token)
    return plaintext.decode("utf-8")


def looks_encrypted(text: str) -> bool:
    """Cheap check: does ``text`` start with the encryption envelope?

    Used by readers to pick decrypt vs. parse-as-JSONL without a try/except.
    """
    return text.startswith(_ENVELOPE_PREFIX)


def read_trace_text(path: Path, *, home: Path | None = None) -> str:
    """Read a trace file as plaintext JSONL, decrypting on the fly.

    Use this everywhere code today does ``path.read_text()`` on a trace
    file. The dashboard, ``loupe show``, ``loupe export``, attribution,
    and the indexer all route through here so encryption is invisible
    to downstream consumers.
    """
    raw = path.read_text(encoding="utf-8")
    if not looks_encrypted(raw):
        return raw
    # Strip a trailing newline before decrypt (we write the envelope +
    # newline). The Fernet token itself is base64, so embedded newlines
    # would corrupt it — the cap on a single envelope per file holds.
    line = raw.strip()
    return decrypt_payload(line, home=home)


__all__ = [
    "EncryptionUnavailable",
    "decrypt_payload",
    "encrypt_payload",
    "encryption_enabled",
    "get_key_path",
    "get_or_create_key",
    "looks_encrypted",
    "read_trace_text",
]
