"""#258 — native Flutter chat must send the bearer token under auth.

The Flutter `chatAuthTokenProvider` hard-returned ``null`` and nothing read it,
so native (mobile/desktop) chat never attached ``Authorization: Bearer`` even
when auth was enabled (web is unaffected — Gatekeeper HttpOnly cookies). This
pins, at the template-render level, that:

* with ``include_auth`` the provider surfaces ``authRepositoryProvider``'s
  access token and the input bar threads it into ``sendMessage``;
* without auth the provider stays a ``null`` no-op and imports nothing from the
  (absent) auth feature.

The compile-level guarantee is ``test_full_generation.test_flutter_full_analyzes``
(``flutter analyze`` on a chat+auth project); this is the fast, flutter-free guard.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from forge.config import BackendConfig, BackendLanguage, FrontendConfig, ProjectConfig
from forge.config._frontend import FrontendFramework
from forge.generator import generate


def _gen_flutter_chat(*, include_auth: bool) -> Path:
    bc = BackendConfig(
        name="api",
        project_name="ChatAuth",
        language=BackendLanguage.PYTHON,
        features=["items"],
        server_port=8000,
    )
    fc = FrontendConfig(
        framework=FrontendFramework.FLUTTER,
        project_name="ChatAuth",
        include_auth=include_auth,
        include_chat=True,
        include_openapi=True,
    )
    cfg = ProjectConfig(project_name="ChatAuth", backends=[bc], frontend=fc)
    cfg.validate()
    root = Path(generate(cfg, quiet=True, dry_run=True))
    # Locate the file directly rather than rglob-ing the directory name: a
    # `test/src/features/chat/presentation/` dir also exists, and rglob order
    # is filesystem-dependent (it picked the wrong one on Windows).
    return next(root.rglob("chat_providers.dart")).parent


def test_chat_token_wired_when_auth_enabled() -> None:
    chat = _gen_flutter_chat(include_auth=True)
    providers = (chat / "chat_providers.dart").read_text(encoding="utf-8")
    input_bar = (chat / "chat_input_bar.dart").read_text(encoding="utf-8")

    # Provider resolves the real token from the auth feature.
    assert "import '../../auth/data/auth_repository.dart';" in providers
    assert "ref.read(authRepositoryProvider).accessToken" in providers
    assert "return null;" not in providers.split("chatAuthTokenProvider")[1][:400]

    # Input bar reads it and threads it into sendMessage.
    assert "ref.watch(chatAuthTokenProvider)" in input_bar
    assert "bearerToken: ref.read(chatAuthTokenProvider).value" in input_bar


def test_chat_token_is_null_noop_without_auth() -> None:
    chat = _gen_flutter_chat(include_auth=False)
    providers = (chat / "chat_providers.dart").read_text(encoding="utf-8")
    input_bar = (chat / "chat_input_bar.dart").read_text(encoding="utf-8")

    # No hard dependency on the absent auth feature.
    assert "auth_repository.dart" not in providers
    assert "authRepositoryProvider" not in providers
    # Provider stays a null no-op; input bar passes no bearer token.
    block = providers.split("chatAuthTokenProvider")[1][:400]
    assert "return null;" in block
    assert "bearerToken" not in input_bar
