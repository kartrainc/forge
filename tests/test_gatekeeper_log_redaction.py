"""Tests for the Gatekeeper credential-log redaction helpers (WS-2.6).

Two leaks block safe observability (WS-10.3 builds dashboards over these
logs):

  (a) the access-log middleware records the full query string, including the
      OIDC callback ``?code=...&state=...`` (and any ``refresh_token``),
      writing OAuth secrets straight into the access log;
  (b) raw opaque session IDs are logged verbatim on session issue / delete /
      decrypt-failure -- a session id IS the bearer credential, so a log
      reader holds a live session.

The fix is a PURE, stdlib-only redaction module
(``src/app/middleware/log_redaction.py``) so the logic is unit-testable in
forge CI without fastapi/redis -- mirrors the way ``tests/test_mcp_audit.py``
and ``tests/test_gatekeeper_oidc_pkce.py`` importlib-load a single
dependency-free module straight from the template path.

BEHAVIORAL tests below load ONLY that pure module. STRUCTURAL (source-
assertion) tests at the bottom mirror ``tests/test_features_auth_gatekeeper.py``
and assert the wiring: the access formatter runs query params through the
redactor, and ``server_session.py`` fingerprints session ids instead of
logging them raw.
"""

from __future__ import annotations

import importlib.util
import re
import sys
from pathlib import Path

import pytest

# -- pure-module loader (importlib, dep-free) --------------------------------

_GK_MIDDLEWARE = (
    Path(__file__).resolve().parent.parent
    / "forge"
    / "features"
    / "auth"
    / "templates"
    / "platform_auth_gatekeeper"
    / "all"
    / "files"
    / "deploy"
    / "infra"
    / "gatekeeper"
    / "src"
    / "app"
    / "middleware"
)

_GK_GATEKEEPER = (
    Path(__file__).resolve().parent.parent
    / "forge"
    / "features"
    / "auth"
    / "templates"
    / "platform_auth_gatekeeper"
    / "all"
    / "files"
    / "deploy"
    / "infra"
    / "gatekeeper"
    / "src"
    / "app"
    / "gatekeeper"
)


def _load_redaction_module():
    path = _GK_MIDDLEWARE / "log_redaction.py"
    spec = importlib.util.spec_from_file_location("gk_log_redaction_under_test", path)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    sys.modules["gk_log_redaction_under_test"] = module
    spec.loader.exec_module(module)
    return module


@pytest.fixture(scope="module")
def redaction():
    return _load_redaction_module()


REDACTED = "<redacted>"

# The denylist of sensitive query-param keys. OAuth authorization-code grant
# secrets (code, state), every token flavour, and generic credential params.
SENSITIVE_KEYS = (
    "code",
    "state",
    "refresh_token",
    "access_token",
    "id_token",
    "token",
    "client_secret",
    "password",
    "authorization",
)


# -- redact_query_params (dict input) ----------------------------------------


class TestRedactQueryParamsDict:
    def test_oidc_callback_code_and_state_redacted(self, redaction) -> None:
        out = redaction.redact_query_params(
            {"code": "super-secret-auth-code", "state": "csrf-state-token"}
        )
        assert out == {"code": REDACTED, "state": REDACTED}

    def test_refresh_and_access_token_redacted(self, redaction) -> None:
        out = redaction.redact_query_params({"refresh_token": "rt-abc", "access_token": "at-xyz"})
        assert out == {"refresh_token": REDACTED, "access_token": REDACTED}

    def test_id_token_and_authorization_redacted(self, redaction) -> None:
        out = redaction.redact_query_params(
            {"id_token": "jwt.body.sig", "authorization": "Bearer abc"}
        )
        assert out == {"id_token": REDACTED, "authorization": REDACTED}

    def test_all_sensitive_keys_redacted(self, redaction) -> None:
        params = {k: f"secret-{k}" for k in SENSITIVE_KEYS}
        out = redaction.redact_query_params(params)
        expected = dict.fromkeys(SENSITIVE_KEYS, REDACTED)
        assert out == expected
        # The original secret value never survives.
        leaked = [v for v in out.values() if v != REDACTED]
        assert leaked == []

    def test_keys_matched_case_insensitively(self, redaction) -> None:
        out = redaction.redact_query_params(
            {"Code": "x", "STATE": "y", "Refresh_Token": "z", "Authorization": "w"}
        )
        assert out == {
            "Code": REDACTED,
            "STATE": REDACTED,
            "Refresh_Token": REDACTED,
            "Authorization": REDACTED,
        }

    def test_benign_params_pass_through(self, redaction) -> None:
        out = redaction.redact_query_params(
            {"tenant": "acme", "redirect_uri": "/dashboard", "page": "2"}
        )
        assert out == {"tenant": "acme", "redirect_uri": "/dashboard", "page": "2"}

    def test_mixed_redacts_only_sensitive(self, redaction) -> None:
        out = redaction.redact_query_params(
            {"code": "leak-me", "tenant": "acme", "state": "leak-too", "page": "1"}
        )
        assert out == {
            "code": REDACTED,
            "tenant": "acme",
            "state": REDACTED,
            "page": "1",
        }

    def test_empty_mapping(self, redaction) -> None:
        assert redaction.redact_query_params({}) == {}

    def test_denylist_not_allowlist_new_param_logs(self, redaction) -> None:
        """A brand-new, non-sensitive param must still appear (denylist, not
        allowlist) so observability isn't silently lost for new query keys."""
        out = redaction.redact_query_params({"brand_new_param": "visible"})
        assert out == {"brand_new_param": "visible"}


# -- redact_query_params (string input) --------------------------------------


class TestRedactQueryParamsString:
    def test_raw_querystring_redacts_secrets(self, redaction) -> None:
        out = redaction.redact_query_params("code=abc&state=xyz&tenant=acme")
        assert isinstance(out, str)
        assert "abc" not in out
        assert "xyz" not in out
        assert "tenant=acme" in out
        assert out.count(REDACTED) == 2

    def test_string_input_returns_string(self, redaction) -> None:
        out = redaction.redact_query_params("page=2")
        assert isinstance(out, str)
        assert "page=2" in out


# -- session_fp --------------------------------------------------------------


class TestSessionFingerprint:
    def test_truncates_to_prefix(self, redaction) -> None:
        sid = "abcdefghijklmnopqrstuvwxyz0123456789"
        fp = redaction.session_fp(sid)
        assert fp == "abcdefgh..."
        assert fp.startswith(sid[:8])

    def test_never_returns_full_id(self, redaction) -> None:
        sid = "a" * 16 + "b" * 16  # distinct tail so leakage is detectable
        fp = redaction.session_fp(sid)
        assert sid not in fp
        assert "b" not in fp  # none of the tail survives
        assert len(fp) < len(sid)

    def test_none_safe(self, redaction) -> None:
        out = redaction.session_fp(None)
        # Must not raise and must not be a real fingerprint.
        assert out is not None
        assert isinstance(out, str)

    def test_empty_string_safe(self, redaction) -> None:
        out = redaction.session_fp("")
        assert isinstance(out, str)
        # No real fingerprint to show.
        assert "..." not in out or out == "..."

    def test_short_id_not_overrun(self, redaction) -> None:
        out = redaction.session_fp("abc")
        # Whatever the policy, the full short id's secret value is still capped:
        # the helper never appends characters that weren't in the id, and never
        # exposes more than the 8-char prefix.
        assert out.replace("...", "") == "abc"


# -- redact_url --------------------------------------------------------------


class TestRedactUrl:
    def test_password_redacted(self, redaction) -> None:
        out = redaction.redact_url("redis://:s3cr3t@redis:6379/0")
        assert out == "redis://:<redacted>@redis:6379/0"

    def test_user_kept_password_redacted(self, redaction) -> None:
        out = redaction.redact_url("redis://default:s3cr3t@redis.example.com:6380/2")
        assert out == "redis://default:<redacted>@redis.example.com:6380/2"
        assert "s3cr3t" not in out

    def test_url_without_password_unchanged(self, redaction) -> None:
        assert redaction.redact_url("redis://redis:6379") == "redis://redis:6379"

    def test_unparsable_url_redacted_whole(self, redaction) -> None:
        assert redaction.redact_url("redis://:pw@[::1") == REDACTED


# -- purity guard ------------------------------------------------------------


def test_log_redaction_module_is_stdlib_only() -> None:
    """The module is importlib-loaded by forge CI without fastapi/redis, so any
    heavy runtime import would break this whole suite."""
    src = (_GK_MIDDLEWARE / "log_redaction.py").read_text(encoding="utf-8")
    forbidden = (
        "import fastapi",
        "from fastapi",
        "import redis",
        "import starlette",
        "from starlette",
        "from app.",
        "import jwt",
    )
    hits = [f for f in forbidden if f in src]
    assert hits == [], f"log_redaction.py must stay stdlib-only -- found {hits}"


# -- STRUCTURAL: access-log middleware wiring --------------------------------


def _middleware_src(module: str) -> str:
    return (_GK_MIDDLEWARE / module).read_text(encoding="utf-8")


def test_access_formatter_runs_query_through_redactor() -> None:
    """The access-log formatter must redact query params before logging -- it
    must NOT log the raw ``dict(request.query_params)`` (which carries the OIDC
    ``code``/``state`` and any ``refresh_token``)."""
    src = _middleware_src("logging.py")
    # The redactor is imported and invoked.
    assert "redact_query_params" in src, "logging.py must import and call redact_query_params"
    assert "redact_query_params(" in src, "redact_query_params must be CALLED"
    # The query value must be the redactor's output. The raw
    # ``dict(request.query_params)`` may only appear as the ARGUMENT to the
    # redactor -- never logged directly. Assert the unwrapped form is gone and
    # the wrapped form is present.
    assert "redact_query_params(dict(request.query_params))" in src, (
        "the access-log query field must be redact_query_params(dict(...))"
    )
    bare_dump = src.replace("redact_query_params(dict(request.query_params))", "")
    assert "dict(request.query_params)" not in bare_dump, (
        "logging.py must not log the raw dict(request.query_params) outside the "
        "redactor -- it leaks the OIDC callback code/state and refresh_token"
    )


# -- STRUCTURAL: server_session.py fingerprints session ids ------------------


def _server_session_src() -> str:
    return (_GK_GATEKEEPER / "server_session.py").read_text(encoding="utf-8")


def test_server_session_uses_fingerprint_helper() -> None:
    """The fingerprint helper must be imported and actually called."""
    src = _server_session_src()
    assert "session_fp" in src, "server_session.py must use session_fp for logs"
    assert "session_fp(" in src, "session_fp must be CALLED"


def _bare_session_id_log_args(src: str) -> list[int]:
    """Return 1-based line numbers where a raw ``session_id`` is passed as a
    positional argument to a nearby logger call -- i.e. the full opaque secret
    reaches the log line. After WS-2.6 every such site reads
    ``session_fp(session_id),`` so this list must be empty."""
    lines = src.splitlines()
    offenders: list[int] = []
    for i, line in enumerate(lines):
        if line.strip() != "session_id,":
            continue
        window = "\n".join(lines[max(0, i - 6) : i + 1])
        if "logger." in window:
            offenders.append(i + 1)
    return offenders


def test_server_session_never_logs_raw_session_id() -> None:
    """No logger call may interpolate the bare ``session_id`` variable -- a
    session id is the bearer credential and must never reach a log line."""
    src = _server_session_src()
    offenders = _bare_session_id_log_args(src)
    assert offenders == [], (
        f"server_session.py lines {offenders}: a raw `session_id` is passed to "
        "a logger call -- use session_fp(session_id) instead"
    )


def test_server_session_keeps_correlation_field() -> None:
    """The log lines must keep a ``session_id=%s`` field for correlation
    (now fed ``session_fp(session_id)``)."""
    src = _server_session_src()
    assert "session_id=%s" in src, (
        "log lines should keep a session_id=%s field for correlation "
        "(now fed session_fp(session_id))"
    )


def test_sensitive_session_log_calls_are_fingerprinted() -> None:
    """The three sensitive log sites -- issue, delete, decrypt-failure -- must
    each feed the fingerprint, not the raw id, to the ``session_id=%s`` field."""
    src = _server_session_src()
    # Each sensitive log message keyword must co-occur with session_fp in its
    # logger call. Find the logger.* call spanning each keyword and assert the
    # fingerprint is used within it.
    for keyword in (
        "server_session_issued",
        "server_session_deleted",
        "server_session_decrypt_failed",
    ):
        idx = src.find(keyword)
        assert idx != -1, f"missing expected log line for {keyword!r}"
        # Grab the enclosing logger call: from the preceding 'logger.' to a
        # generous forward window.
        start = src.rfind("logger.", 0, idx)
        assert start != -1, f"{keyword!r} not inside a logger call"
        call = src[start : idx + 300]
        assert "session_fp(" in call, (
            f"the {keyword!r} log call must feed session_fp(session_id), not the raw session_id"
        )
        # Defensive: the raw `session_id,` positional must not appear here.
        assert not re.search(r"\n\s*session_id,\s*\n", call), (
            f"the {keyword!r} log call still passes a bare session_id"
        )


# -- STRUCTURAL: redis.py logs the URL without its password --------------------


def test_redis_client_never_logs_the_raw_url() -> None:
    src = (_GK_GATEKEEPER / "redis.py").read_text(encoding="utf-8")
    log_calls = re.findall(r"logger\.\w+\((?:[^()]|\([^()]*\))*\)", src)
    url_calls = [c for c in log_calls if "redis_url" in c]
    assert url_calls, "redis.py should still log the Redis endpoint"
    raw = [c for c in url_calls if not re.search(r"redact_url\([^)]*redis_url\)", c)]
    assert raw == [], f"redis.py logs a Redis URL without redact_url(): {raw}"
