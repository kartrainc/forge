"""Targeted tests for the less-exercised parts of forge.cli.

Covers:
- _is_headless flags (json_output, no_docker, backend_port, etc.)
- _ask_* wrappers (None => sys.exit(1) path)
- _ask_features loop (empty + invalid inputs retry)
- _ask_port loop (non-numeric + out-of-range retry)
- _prompt_backend dispatching via BACKEND_REGISTRY
- _print_completion for each shell
- _json_error envelope writer
- main() JSON success envelope + headless ValueError paths + aborted confirm
"""

from __future__ import annotations

import io
import json
import sys
from argparse import Namespace
from unittest.mock import patch

import pytest

from forge import cli


def _default_args(**overrides):
    defaults = dict(
        config=None,
        project_name=None,
        description=None,
        output_dir=".",
        backend_language=None,
        backend_name=None,
        backend_port=None,
        python_version=None,
        node_version=None,
        rust_edition=None,
        frontend=None,
        features=None,
        author_name=None,
        package_manager=None,
        frontend_port=None,
        color_scheme=None,
        org_name=None,
        include_auth=None,
        include_chat=None,
        include_openapi=None,
        generate_e2e_tests=None,
        keycloak_port=None,
        keycloak_realm=None,
        keycloak_client_id=None,
        yes=False,
        no_docker=False,
        quiet=False,
        verbose=False,
        json_output=False,
        completion=None,
    )
    defaults.update(overrides)
    return Namespace(**defaults)


# -- _is_headless edge cases --------------------------------------------------


class TestIsHeadlessEdges:
    def test_json_output_alone(self) -> None:
        assert cli._is_headless(_default_args(json_output=True))

    def test_quiet_alone(self) -> None:
        assert cli._is_headless(_default_args(quiet=True))

    def test_no_docker_alone(self) -> None:
        assert cli._is_headless(_default_args(no_docker=True))

    def test_backend_port_alone(self) -> None:
        assert cli._is_headless(_default_args(backend_port=5050))

    def test_python_version_alone(self) -> None:
        assert cli._is_headless(_default_args(python_version="3.13"))

    def test_description_alone(self) -> None:
        assert cli._is_headless(_default_args(description="x"))

    def test_features_alone(self) -> None:
        assert cli._is_headless(_default_args(features="items"))


# -- _ask_* wrappers: None (user cancelled) triggers SystemExit ---------------


class _FakeAsk:
    """Stand-in for questionary's builder chain — `.ask()` returns a preset value."""

    def __init__(self, value) -> None:
        self._value = value

    def ask(self):
        return self._value


class TestAskHelpersCancel:
    def test_ask_text_none_exits(self) -> None:
        with patch("forge.cli.questionary.text", return_value=_FakeAsk(None)):
            with pytest.raises(SystemExit) as exc:
                cli._ask_text("q")
        assert exc.value.code == 1

    def test_ask_confirm_none_exits(self) -> None:
        with patch("forge.cli.questionary.confirm", return_value=_FakeAsk(None)):
            with pytest.raises(SystemExit) as exc:
                cli._ask_confirm("q")
        assert exc.value.code == 1

    def test_ask_select_none_exits(self) -> None:
        with patch("forge.cli.questionary.select", return_value=_FakeAsk(None)):
            with pytest.raises(SystemExit) as exc:
                cli._ask_select("q", choices=["a"])
        assert exc.value.code == 1

    def test_ask_text_returns_value(self) -> None:
        with patch("forge.cli.questionary.text", return_value=_FakeAsk("hello")):
            assert cli._ask_text("q") == "hello"

    def test_ask_confirm_returns_value(self) -> None:
        with patch("forge.cli.questionary.confirm", return_value=_FakeAsk(True)):
            assert cli._ask_confirm("q") is True

    def test_ask_select_returns_value(self) -> None:
        with patch("forge.cli.questionary.select", return_value=_FakeAsk("pick")):
            assert cli._ask_select("q", choices=["pick"]) == "pick"


# -- _ask_features / _ask_port retry loops ------------------------------------


class TestAskFeaturesLoop:
    def test_empty_then_valid(self, capsys) -> None:
        with patch("forge.cli.interactive._ask_text", side_effect=["  ", "items, orders"]):
            result = cli._ask_features()
        assert result == ["items", "orders"]
        assert "at least one feature" in capsys.readouterr().out

    def test_invalid_then_valid(self, capsys) -> None:
        # "2bad" fails validate_features (must start with letter), then valid input succeeds.
        with patch("forge.cli.interactive._ask_text", side_effect=["2bad", "widgets"]):
            result = cli._ask_features()
        assert result == ["widgets"]
        assert "Invalid" in capsys.readouterr().out


class TestAskPortLoop:
    def test_out_of_range_then_valid(self, capsys) -> None:
        with patch("forge.cli.interactive._ask_text", side_effect=["1", "5000"]):
            assert cli._ask_port("port:", default="5000") == 5000
        assert "between 1024 and 65535" in capsys.readouterr().out

    def test_non_numeric_then_valid(self, capsys) -> None:
        with patch("forge.cli.interactive._ask_text", side_effect=["abc", "5000"]):
            assert cli._ask_port("port:", default="5000") == 5000


# -- _prompt_backend ---------------------------------------------------------


class TestPromptBackend:
    def test_python_backend_driven_by_registry(self) -> None:
        # Simulate: name="api", select python, port=5000, select "3.13", features="items".
        text_responses = iter(["api", "5000", "items"])
        select_responses = iter(["Python (FastAPI)", "3.13"])

        def fake_text(prompt, default="", **_):
            return next(text_responses)

        def fake_select(prompt, choices, **_):
            return next(select_responses)

        with patch("forge.cli.interactive._ask_text", side_effect=fake_text):
            with patch("forge.cli.interactive._ask_select", side_effect=fake_select):
                bc = cli._prompt_backend(0, "Demo", "desc", default_port=5000)

        assert bc.name == "api"
        assert bc.language.value == "python"
        assert bc.python_version == "3.13"
        assert bc.server_port == 5000
        assert bc.features == ["items"]

    def test_rust_backend_sets_rust_edition(self) -> None:
        text_responses = iter(["rusty", "5001", "items"])
        select_responses = iter(["Rust (Axum)", "2024"])

        with (
            patch(
                "forge.cli.interactive._ask_text",
                side_effect=lambda *a, **kw: next(text_responses),
            ),
            patch(
                "forge.cli.interactive._ask_select",
                side_effect=lambda *a, **kw: next(select_responses),
            ),
        ):
            bc = cli._prompt_backend(1, "Demo", "desc", default_port=5001)

        assert bc.language.value == "rust"
        assert bc.rust_edition == "2024"


# -- _print_completion --------------------------------------------------------


class TestPrintCompletion:
    @pytest.mark.parametrize("shell", ["bash", "zsh", "fish"])
    def test_prints_and_exits(self, shell: str, capsys) -> None:
        with pytest.raises(SystemExit) as exc:
            cli._print_completion(shell)
        assert exc.value.code == 0
        out = capsys.readouterr().out
        assert "forge" in out.lower()


# -- _json_error --------------------------------------------------------------


class TestJsonError:
    def test_writes_envelope_and_exits(self) -> None:
        buf = io.StringIO()
        with pytest.raises(SystemExit) as exc:
            cli._json_error(buf, "kaboom")
        assert exc.value.code == 2
        envelope = json.loads(buf.getvalue().strip())
        assert envelope == {"error": "kaboom"}


# -- main(): JSON success envelope + config errors in JSON/text modes --------


class TestMainIntegration:
    def test_json_success_envelope(self, tmp_path, monkeypatch, capsys) -> None:
        monkeypatch.setattr(
            sys,
            "argv",
            [
                "forge",
                "--json",
                "--yes",
                "--no-docker",
                "--project-name",
                "JsonOk",
                "--output-dir",
                str(tmp_path),
                "--frontend",
                "vue",
                "--no-auth",
                "--features",
                "items",
                "--backend-language",
                "python",
            ],
        )

        fake_root = tmp_path / "jsonok"
        fake_root.mkdir()
        with patch("forge.cli.main.generate", return_value=fake_root):
            cli.main()

        out = capsys.readouterr().out
        envelope = json.loads(out.strip().splitlines()[-1])
        # Pre-#5 keys remain — back-compat for legacy consumers.
        assert envelope["project_root"] == str(fake_root)
        assert envelope["framework"] == "vue"
        assert envelope["features"] == ["items"]
        assert envelope["backends"][0]["language"] == "python"
        # Init #5 — the new ``report`` key is present and self-consistent.
        # The mocked generate doesn't populate it, so most fields are
        # empty, but the contract (schema version + key set) holds.
        assert "report" in envelope
        report = envelope["report"]
        assert report["_report_version"] == 1
        # Every documented key is present even when empty so consumers
        # can rely on the shape.
        for key in (
            "project_root",
            "effective_config",
            "option_origins",
            "fragment_graph",
            "file_inventory",
            "provenance_sidecar_paths",
            "warnings",
            "skipped_toolchains",
            "next_actions",
            "hidden_mutations",
        ):
            assert key in report, f"missing key {key!r} in report"

    def test_json_envelope_captures_hidden_mutation(
        self, tmp_path, monkeypatch, capsys
    ) -> None:
        """The CLI rewrites auth.mode to 'none' when Keycloak is
        disabled — Init #5 surfaces that coercion under
        ``report.hidden_mutations``."""
        monkeypatch.setattr(
            sys,
            "argv",
            [
                "forge",
                "--json",
                "--yes",
                "--no-docker",
                "--project-name",
                "Mute",
                "--output-dir",
                str(tmp_path),
                "--frontend",
                "none",
                "--no-auth",
                "--backend-language",
                "python",
                "--set",
                "auth.mode=generate",
            ],
        )

        fake_root = tmp_path / "mute"
        fake_root.mkdir()
        with patch("forge.cli.main.generate", return_value=fake_root):
            cli.main()

        envelope = json.loads(capsys.readouterr().out.strip().splitlines()[-1])
        mutations = envelope["report"]["hidden_mutations"]
        assert any(m["path"] == "auth.mode" for m in mutations), mutations
        m = next(m for m in mutations if m["path"] == "auth.mode")
        assert m["previous"] == "generate"
        assert m["current"] == "none"

    def test_json_config_load_failure(self, tmp_path, monkeypatch, capsys) -> None:
        missing = tmp_path / "missing.yaml"
        monkeypatch.setattr(
            sys,
            "argv",
            [
                "forge",
                "--json",
                "--yes",
                "--no-docker",
                "--config",
                str(missing),
                "--output-dir",
                str(tmp_path),
            ],
        )
        with pytest.raises(SystemExit) as exc:
            cli.main()
        assert exc.value.code == 2
        envelope = json.loads(capsys.readouterr().out.strip().splitlines()[-1])
        assert "error" in envelope

    def test_text_config_load_failure(self, tmp_path, monkeypatch, capsys) -> None:
        missing = tmp_path / "missing.yaml"
        monkeypatch.setattr(
            sys,
            "argv",
            [
                "forge",
                "--yes",
                "--quiet",
                "--no-docker",
                "--config",
                str(missing),
                "--output-dir",
                str(tmp_path),
            ],
        )
        with pytest.raises(SystemExit) as exc:
            cli.main()
        assert exc.value.code == 2
        err = capsys.readouterr().err
        assert "Configuration error" in err

    def test_json_validation_failure(self, tmp_path, monkeypatch, capsys) -> None:
        # Invalid port triggers ProjectConfig.validate() ValueError.
        monkeypatch.setattr(
            sys,
            "argv",
            [
                "forge",
                "--json",
                "--yes",
                "--no-docker",
                "--project-name",
                "BadPort",
                "--output-dir",
                str(tmp_path),
                "--frontend",
                "none",
                "--no-auth",
                "--backend-port",
                "80",  # below 1024 — should be rejected
            ],
        )
        with pytest.raises(SystemExit) as exc:
            cli.main()
        assert exc.value.code == 2
        envelope = json.loads(capsys.readouterr().out.strip().splitlines()[-1])
        assert "error" in envelope

    def test_aborted_confirm_exits_zero(self, tmp_path, monkeypatch, capsys) -> None:
        # No --yes; confirm returns False; should exit 0 with "Aborted."
        # HF-3 gates the confirm prompt behind ``sys.stdin.isatty()`` so an
        # agent without a TTY gets a structured refusal rather than the
        # questionary silent-exit; fake a TTY here to keep exercising the
        # abort-after-confirm branch.
        class _TTY:
            def isatty(self) -> bool:
                return True

        monkeypatch.setattr(sys, "stdin", _TTY())
        monkeypatch.setattr(
            sys,
            "argv",
            [
                "forge",
                "--no-docker",
                "--project-name",
                "Abort",
                "--output-dir",
                str(tmp_path),
                "--frontend",
                "none",
                "--no-auth",
            ],
        )
        with patch("forge.cli.interactive._ask_confirm", return_value=False):
            with pytest.raises(SystemExit) as exc:
                cli.main()
        assert exc.value.code == 0

    def test_completion_flag_prints_and_exits(self, monkeypatch, capsys) -> None:
        monkeypatch.setattr(sys, "argv", ["forge", "--completion", "bash"])
        with pytest.raises(SystemExit) as exc:
            cli.main()
        assert exc.value.code == 0
        assert "forge" in capsys.readouterr().out.lower()


# -- _collect_inputs (interactive) — mocked ask_* layer -----------------------


class _StubStdin:
    """Pretends stdin is a TTY so _collect_inputs doesn't short-circuit."""

    def isatty(self) -> bool:
        return True


class TestCollectInputs:
    def test_no_tty_exits_2(self, monkeypatch, capsys) -> None:
        class _NotTTY:
            def isatty(self) -> bool:
                return False

        monkeypatch.setattr(sys, "stdin", _NotTTY())
        with pytest.raises(SystemExit) as exc:
            cli._collect_inputs()
        assert exc.value.code == 2
        assert "Interactive mode" in capsys.readouterr().err

    def test_python_vue_with_keycloak_path(self, monkeypatch) -> None:
        monkeypatch.setattr(sys, "stdin", _StubStdin())

        texts = iter(
            [
                "My App",  # project name
                "A desc",  # description
                "backend",  # backend name (prompt_backend #1)
                "5000",  # port
                "items",  # features
                "5173",  # frontend port
                "Alice",  # author name (actually author comes before pkg manager in code — check order)
                "18080",  # keycloak port
                "app",  # realm
                "my-app",  # client id
            ]
        )
        selects = iter(
            [
                "Python (FastAPI)",
                "3.13",
                "Vue 3",  # frontend framework
                "npm",  # package manager
                "shadcn-vue (Radix Vue + Tailwind CSS 4)",  # UI kit (Vue offers two)
                "blue",  # color scheme
                "Sidebar App Shell",  # app-shell layout (Vue now offers >1)
            ]
        )
        confirms = iter(
            [
                False,  # add another backend? no
                True,  # enable keycloak auth? yes
                False,  # enable chat? no (asked after the kit, which has chat)
                False,  # enable openapi? no
                True,  # proceed with generation? yes
            ]
        )

        # _ask_text order in _collect_inputs: project_name, description, then in _prompt_backend
        # (name, port, features), then back to _collect_inputs for author_name. So the iter above
        # must align with that order. Let's be explicit.
        text_order = iter(
            [
                "My App",  # project_name
                "A desc",  # description
                "backend",  # _prompt_backend: name
                "5010",  # _prompt_backend: port (5010, not 5000: gatekeeper takes 5000)
                "items",  # _prompt_backend: features (via _ask_features)
                "Alice",  # author_name
                "5173",  # frontend port (via _ask_port)
                "18080",  # keycloak port (via _ask_port)
                "app",  # keycloak realm
                "my-app",  # client id
            ]
        )

        def fake_text(msg, default="", **_):
            return next(text_order)

        with patch("forge.cli.interactive._ask_text", side_effect=fake_text), patch(
            "forge.cli.interactive._ask_select", side_effect=lambda *a, **kw: next(selects)
        ), patch(
            "forge.cli.interactive._ask_confirm",
            side_effect=lambda *a, **kw: next(confirms),
        ):
            config = cli._collect_inputs()

        assert config is not None
        assert config.project_name == "My App"
        assert config.backend is not None
        assert config.backend.language.value == "python"
        assert config.frontend is not None
        assert config.frontend.framework.value == "vue"
        assert config.include_keycloak is True
        assert config.frontend.keycloak_realm == "app"

    def test_decline_confirm_returns_none(self, monkeypatch) -> None:
        monkeypatch.setattr(sys, "stdin", _StubStdin())

        text_order = iter(
            [
                "P",  # project_name
                "D",  # description
                "backend",
                "5000",
                "items",
            ]
        )
        selects = iter(
            [
                "Python (FastAPI)",
                "3.13",
                "None",  # frontend framework = none
            ]
        )
        confirms = iter(
            [
                False,  # add another? no
                False,  # proceed? no -> return None
            ]
        )

        with patch(
            "forge.cli.interactive._ask_text", side_effect=lambda *a, **kw: next(text_order)
        ), patch(
            "forge.cli.interactive._ask_select", side_effect=lambda *a, **kw: next(selects)
        ), patch(
            "forge.cli.interactive._ask_confirm",
            side_effect=lambda *a, **kw: next(confirms),
        ):
            result = cli._collect_inputs()

        assert result is None

    def test_flutter_path(self, monkeypatch) -> None:
        monkeypatch.setattr(sys, "stdin", _StubStdin())

        text_order = iter(
            [
                "F App",  # project_name
                "flutter desc",  # description
                "backend",
                "5000",
                "items",
                "FlutterAuthor",
                "com.myco",  # org_name
            ]
        )
        selects = iter(
            [
                "Python (FastAPI)",
                "3.13",
                "Flutter",  # frontend
                "Sidebar App Shell",  # app-shell layout (Flutter now offers >1)
            ]
        )
        confirms = iter(
            [
                False,  # add another? no
                False,  # enable keycloak? no
                False,  # enable chat? no
                # openapi is auto-True for Flutter (no prompt)
                True,  # proceed? yes
            ]
        )

        with patch(
            "forge.cli.interactive._ask_text", side_effect=lambda *a, **kw: next(text_order)
        ), patch(
            "forge.cli.interactive._ask_select", side_effect=lambda *a, **kw: next(selects)
        ), patch(
            "forge.cli.interactive._ask_confirm",
            side_effect=lambda *a, **kw: next(confirms),
        ):
            config = cli._collect_inputs()

        assert config is not None
        assert config.frontend is not None
        assert config.frontend.framework.value == "flutter"
        assert config.frontend.org_name == "com.myco"
        assert config.include_keycloak is False


# -- main() --verify dispatch ----------------------------------------------
#
# End-to-end coverage that ``forge --verify`` reaches ``verify_project``
# with the right project root + scope + fail_on knobs. The detailed
# algorithm cases live in tests/test_verify.py; here we only assert the
# argparse → dispatcher → exit-code chain.


class TestMainVerifyDispatch:
    def test_verify_dispatch_clean_exit_zero(self, tmp_path, monkeypatch) -> None:
        from forge.sync.manifest import write_forge_toml  # noqa: PLC0415
        from forge.sync.provenance import sha256_of  # noqa: PLC0415

        (tmp_path / "a.py").write_text("hello\n")
        sha = sha256_of(tmp_path / "a.py")
        write_forge_toml(
            tmp_path / "forge.toml",
            version="1.0.0",
            project_name="t",
            templates={"python": "p"},
            options={},
            provenance={"a.py": {"origin": "base-template", "sha256": sha}},
        )
        monkeypatch.setattr(sys, "argv", ["forge", "--verify", "--project-path", str(tmp_path)])
        with pytest.raises(SystemExit) as exc:
            cli.main()
        assert exc.value.code == 0

    def test_verify_dispatch_drift_exit_ten(self, tmp_path, monkeypatch) -> None:
        from forge.sync.manifest import write_forge_toml  # noqa: PLC0415
        from forge.sync.provenance import sha256_of  # noqa: PLC0415

        (tmp_path / "a.py").write_text("hello\n")
        sha = sha256_of(tmp_path / "a.py")
        write_forge_toml(
            tmp_path / "forge.toml",
            version="1.0.0",
            project_name="t",
            templates={"python": "p"},
            options={},
            provenance={"a.py": {"origin": "base-template", "sha256": sha}},
        )
        (tmp_path / "a.py").write_text("user edit\n")
        monkeypatch.setattr(sys, "argv", ["forge", "--verify", "--project-path", str(tmp_path)])
        with pytest.raises(SystemExit) as exc:
            cli.main()
        assert exc.value.code == 10

    def test_verify_scope_propagates_to_verify_project(self, tmp_path, monkeypatch) -> None:
        """--verify-scope files must reach verify_project as scope='files'."""
        from forge.sync.manifest import write_forge_toml  # noqa: PLC0415

        write_forge_toml(
            tmp_path / "forge.toml",
            version="1.0.0",
            project_name="t",
            templates={"python": "p"},
            options={},
        )
        monkeypatch.setattr(
            sys,
            "argv",
            [
                "forge",
                "--verify",
                "--verify-scope",
                "files",
                "--project-path",
                str(tmp_path),
            ],
        )
        captured: dict = {}

        def fake_verify(project_root, *, scope, fail_on):
            captured["scope"] = scope
            captured["fail_on"] = fail_on
            captured["root"] = project_root
            from forge.sync.project_to_forge.verify import VerifyReport  # noqa: PLC0415

            return VerifyReport(
                worst="clean",
                summary={
                    "unchanged": 0,
                    "user-modified": 0,
                    "missing": 0,
                    "sentinel-corrupt": 0,
                },
            )

        with (
            patch("forge.cli.commands.verify.verify_project", side_effect=fake_verify),
            pytest.raises(SystemExit),
        ):
            cli.main()
        assert captured["scope"] == "files"
        assert captured["fail_on"] == "drift"

    def test_fail_on_conflict_propagates_to_verify_project(self, tmp_path, monkeypatch) -> None:
        """--fail-on conflict must reach verify_project as fail_on='conflict'."""
        from forge.sync.manifest import write_forge_toml  # noqa: PLC0415

        write_forge_toml(
            tmp_path / "forge.toml",
            version="1.0.0",
            project_name="t",
            templates={"python": "p"},
            options={},
        )
        monkeypatch.setattr(
            sys,
            "argv",
            [
                "forge",
                "--verify",
                "--fail-on",
                "conflict",
                "--project-path",
                str(tmp_path),
            ],
        )
        captured: dict = {}

        def fake_verify(project_root, *, scope, fail_on):
            captured["fail_on"] = fail_on
            from forge.sync.project_to_forge.verify import VerifyReport  # noqa: PLC0415

            return VerifyReport(
                worst="clean",
                summary={
                    "unchanged": 0,
                    "user-modified": 0,
                    "missing": 0,
                    "sentinel-corrupt": 0,
                },
            )

        with (
            patch("forge.cli.commands.verify.verify_project", side_effect=fake_verify),
            pytest.raises(SystemExit),
        ):
            cli.main()
        assert captured["fail_on"] == "conflict"

    def test_verify_missing_forge_toml_exits_5(self, tmp_path, monkeypatch) -> None:
        monkeypatch.setattr(sys, "argv", ["forge", "--verify", "--project-path", str(tmp_path)])
        with pytest.raises(SystemExit) as exc:
            cli.main()
        assert exc.value.code == 5
