"""Invariants for the Vue session-timeout fragment (Phase 8).

The fragment ships ``useSessionTimeout`` composable + ``SessionTimeoutModal``
component into the active Vue frontend tree, implementing the SPA
half of platform's BFF + session-timeout RFC verbatim. The non-trivial
design constraints — drift-immune countdown, cross-tab leader election,
visibility gating, activity debounce — must all be present in the
shipped source for the runtime to behave correctly.

Behavioural verification (the actual countdown drift, BroadcastChannel
dedup, modal open/close at warnAt) lives in the Vue unit tests and
Playwright e2e once Phase 9 lands. This file gates the *structural*
correctness of what gets shipped.

Cross-reference: implementation plan at
``~/.claude/plans/review-the-c-users-chifo-work-platform-a-pure-torvalds.md``
(Phase 8 deliverables; platform RFC at
``~/.claude/plans/analyze-the-following-issue-lovely-sonnet.md``
sections "Activity model" + "SPA pattern").
"""

from __future__ import annotations

from pathlib import Path

from forge.config import BackendLanguage
from forge.fragments import FRAGMENT_REGISTRY


def _fragment_root() -> Path:
    frag = FRAGMENT_REGISTRY["platform_auth_session_timeout_vue"]
    impl = frag.implementations[BackendLanguage.PYTHON]
    return Path(impl.fragment_dir) / "files" / "src"


def _composable_path() -> Path:
    return _fragment_root() / "shared" / "composables" / "useSessionTimeout.ts"


def _modal_path() -> Path:
    # The dialog is a fragment of its own over the composable (ADR-015).
    frag = FRAGMENT_REGISTRY["platform_auth_session_dialog_vue"]
    root = Path(frag.implementations[BackendLanguage.PYTHON].fragment_dir) / "files" / "src"
    return root / "features" / "auth" / "components" / "SessionTimeoutModal.vue"


def test_the_dialog_is_a_fragment_over_the_composable() -> None:
    dialog = FRAGMENT_REGISTRY["platform_auth_session_dialog_vue"]
    assert dialog.depends_on == ("platform_auth_session_timeout_vue",)
    assert dialog.target_frontends == FRAGMENT_REGISTRY["platform_auth_session_timeout_vue"].target_frontends
    # The composable's fragment no longer carries the dialog.
    assert not (_fragment_root() / "features").exists()


def test_session_timeout_vue_fragment_registered() -> None:
    from forge.config import FrontendFramework

    assert "platform_auth_session_timeout_vue" in FRAGMENT_REGISTRY
    frag = FRAGMENT_REGISTRY["platform_auth_session_timeout_vue"]
    # Project-scoped, language-agnostic via the Python implementation
    # key (matches mcp_ui_svelte / mcp_ui_flutter pattern).
    impl = frag.implementations[BackendLanguage.PYTHON]
    assert impl.scope == "project"
    # Frontend gating — the fragment ships `.vue` + `useSessionTimeout.ts`
    # files under `apps/frontend/src/...`; emitting them to a Svelte or
    # Flutter (or no-frontend) project would dump orphan files. Pin the
    # gating here so a regression that drops `target_frontends` resurfaces
    # the cross-frontend pollution bug we fixed last turn.
    assert frag.target_frontends == (FrontendFramework.VUE,), (
        "Vue session-timeout fragment must declare target_frontends=(VUE,) "
        "to prevent shipping to non-Vue projects"
    )


def test_composable_and_modal_files_shipped() -> None:
    """Both files must land in the conventional Vue paths."""
    assert _composable_path().is_file(), (
        f"useSessionTimeout.ts missing at {_composable_path()}"
    )
    assert _modal_path().is_file(), (
        f"SessionTimeoutModal.vue missing at {_modal_path()}"
    )


def test_composable_implements_drift_immune_countdown() -> None:
    """Countdown must be Date.now()-based, NOT a decrementing integer.

    Per platform RFC §"SPA pattern" point 1: Chrome throttles
    setInterval to 1Hz hidden tabs and progressively to 1 wake/min
    under Throttled Wake-Ups. A decrementing integer drifts visibly
    when the user returns. The fix is to store an absolute target
    (``idleExpiresAt``) and compute remaining via ``Date.now()`` at
    read time.
    """
    text = _composable_path().read_text(encoding="utf-8")
    assert "idleExpiresAt" in text, (
        "Composable must store an absolute target timestamp, not a "
        "decrementing integer (drift-immune countdown contract)"
    )
    # The remaining-seconds computation must reference Date.now().
    assert "Date.now()" in text, (
        "Composable must compute remaining from Date.now() at read "
        "time, not from a counter that decrements on tick"
    )


def test_composable_implements_cross_tab_dedup() -> None:
    """BroadcastChannel-based leader election must be present.

    Per platform RFC §"SPA pattern" point 2: a user with N visible
    tabs would fire N concurrent extension POSTs on the same mouse
    move. BroadcastChannel elects a leader; only one tab POSTs per
    activity burst.
    """
    text = _composable_path().read_text(encoding="utf-8")
    assert "BroadcastChannel" in text, (
        "Composable must use BroadcastChannel for cross-tab dedup"
    )
    # The two message types from the RFC: claim + extended.
    assert "activity-claim" in text or "'activity-claim'" in text, (
        "Composable must post activity-claim messages for leader election"
    )
    assert "extended" in text, (
        "Composable must post 'extended' messages so siblings sync expiresAt"
    )


def test_composable_visibility_gates_extensions() -> None:
    """Hidden tabs must NOT extend the session.

    Per platform RFC §"SPA pattern" point 3: a hidden tab's
    `mousemove` listeners can fire from outside-window events.
    Every extension is gated on `document.visibilityState === 'visible'`.
    """
    text = _composable_path().read_text(encoding="utf-8")
    assert "visibilityState" in text, (
        "Composable must gate extensions on document.visibilityState"
    )
    assert "'visible'" in text or '"visible"' in text, (
        "Composable must check the literal 'visible' state value"
    )


def test_composable_listens_to_real_activity_events() -> None:
    """Activity events must be the four the RFC specifies.

    Per platform RFC: ``mousemove``, ``keydown``, ``scroll``,
    ``visibilitychange``. Background HTTP traffic must NOT count
    as activity (the explicit anti-pattern Auth0/Okta document).
    """
    text = _composable_path().read_text(encoding="utf-8")
    for event in ("mousemove", "keydown", "scroll", "visibilitychange"):
        assert event in text, (
            f"Composable must listen to '{event}' user-interaction event "
            f"(per platform RFC §'Activity model')"
        )


def test_composable_debounces_extensions() -> None:
    """Activity bursts must be debounced.

    Per platform RFC: 30-second debounce on extension POSTs to
    prevent hammering the server-side rate limit (4/min/session)
    on every mouse twitch.
    """
    text = _composable_path().read_text(encoding="utf-8")
    # The default constant from the RFC is 30_000 ms.
    assert "30_000" in text or "30000" in text, (
        "Composable must default to 30-second debounce window"
    )


def test_composable_silently_disables_when_bootstrap_fails() -> None:
    """Composable must no-op on 401 or when timeouts=0.

    Per platform RFC: the composable is inert on unauthenticated
    routes (no cookie → bootstrap returns 401 → no listeners
    attached, no channel opened) AND when the server has timeouts
    disabled (``idle_timeout_seconds === 0 && absolute_timeout_seconds === 0``).
    """
    text = _composable_path().read_text(encoding="utf-8")
    # Both fail-paths must be present.
    assert "enabled" in text, (
        "Composable must expose an `enabled` flag and gate behaviour on it"
    )
    assert "idle_timeout_seconds === 0" in text or "idle_timeout_seconds == 0" in text, (
        "Composable must detect server-disabled timeouts (idle_timeout_seconds=0)"
    )


def test_composable_uses_correct_endpoint_and_method_semantics() -> None:
    """GET /auth/session for read; POST /auth/session for extend.

    Per platform RFC: same path, two methods. GET reads countdown
    (no side-effect), POST extends (touches Redis + rate-limited
    4/min). ``/auth`` ForwardAuth is read-only — never extends.
    """
    text = _composable_path().read_text(encoding="utf-8")
    assert "/auth/session" in text, "Composable must hit /auth/session endpoint"
    assert "credentials: 'include'" in text, (
        "Composable must send credentials so the session cookie is included"
    )
    # POST extends, GET reads. Both should appear.
    assert "method: 'POST'" in text, (
        "Composable must use POST for extension (per RFC method semantics)"
    )


def test_modal_opens_at_warn_threshold_and_visibility_gates() -> None:
    """Modal must open only at idleRemaining <= warnAt AND tab visible."""
    text = _modal_path().read_text(encoding="utf-8")
    assert "idleRemaining" in text, "Modal must consume idleRemaining"
    assert "warnAtSeconds" in text or "warnAt" in text, (
        "Modal must consume the warnAt threshold from the composable"
    )
    assert "visibilityState" in text, (
        "Modal must gate visibility on document.visibilityState"
    )


def test_modal_offers_stay_signed_in_and_sign_out_actions() -> None:
    """Modal must wire both actions per RFC §'SPA pattern'."""
    text = _modal_path().read_text(encoding="utf-8")
    assert "Stay signed in" in text, "Modal must offer 'Stay signed in'"
    assert "Sign out" in text, "Modal must offer 'Sign out'"
    # Stay-signed-in must call extend() (the force-extension path).
    assert ".extend()" in text, (
        "Modal's 'Stay signed in' button must call session.extend() to "
        "force-fire an extension, bypassing the activity debounce"
    )
    # Sign-out must navigate to /logout (existing flow).
    assert "/logout" in text, "Modal's 'Sign out' must navigate to /logout"


def test_modal_imports_from_relative_composable_path() -> None:
    """Modal's import must resolve in a real Vue project tree.

    The fragment ships into ``apps/frontend/src/features/auth/components/``
    with the composable at ``apps/frontend/src/shared/composables/``.
    The relative import must walk three dirs up + into shared/.
    """
    text = _modal_path().read_text(encoding="utf-8")
    assert "../../../shared/composables/useSessionTimeout" in text, (
        "Modal must import useSessionTimeout via the project-relative path"
    )
