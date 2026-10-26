"""Tests for the forge.toml read/write module."""

from __future__ import annotations

from pathlib import Path

import pytest
import tomlkit

from forge.sync.manifest import (
    CURRENT_SCHEMA_VERSION,
    ForgeTomlData,
    read_forge_toml,
    write_forge_toml,
)


class TestWriteForgeToml:
    def test_canonical_shape_round_trips(self, tmp_path: Path) -> None:
        path = tmp_path / "forge.toml"
        write_forge_toml(
            path,
            version="0.2.0",
            project_name="acme",
            templates={"python": "services/python-service-template"},
            options={
                "middleware.rate_limit": True,
                "rag.backend": "qdrant",
                "rag.top_k": 10,
            },
        )
        data = read_forge_toml(path)
        assert data.version == "0.2.0"
        assert data.project_name == "acme"
        assert data.templates == {"python": "services/python-service-template"}
        assert data.options == {
            "middleware.rate_limit": True,
            "rag.backend": "qdrant",
            "rag.top_k": 10,
        }

    def test_option_entries_sorted(self, tmp_path: Path) -> None:
        """Canonical order is alphabetical for diff stability."""
        path = tmp_path / "forge.toml"
        write_forge_toml(
            path,
            version="0.2.0",
            project_name="x",
            templates={"python": "p"},
            options={
                "rag.top_k": 5,
                "middleware.rate_limit": True,
                "observability.tracing": False,
            },
        )
        text = path.read_text(encoding="utf-8")
        # Dotted keys are preserved in quoted form; order must be alpha.
        rate_limit_pos = text.index('"middleware.rate_limit"')
        tracing_pos = text.index('"observability.tracing"')
        top_k_pos = text.index('"rag.top_k"')
        assert rate_limit_pos < tracing_pos < top_k_pos

    def test_empty_options_emits_empty_section(self, tmp_path: Path) -> None:
        path = tmp_path / "forge.toml"
        write_forge_toml(
            path,
            version="0.2.0",
            project_name="bare",
            templates={"python": "p"},
            options={},
        )
        data = read_forge_toml(path)
        assert data.options == {}


class TestReadForgeToml:
    def _write(self, tmp_path: Path, content: str) -> Path:
        path = tmp_path / "forge.toml"
        path.write_text(content, encoding="utf-8")
        return path

    def test_missing_file_raises(self, tmp_path: Path) -> None:
        with pytest.raises(FileNotFoundError, match="not found"):
            read_forge_toml(tmp_path / "ghost.toml")

    def test_missing_forge_section_raises(self, tmp_path: Path) -> None:
        path = self._write(tmp_path, "[something_else]\nx = 1\n")
        with pytest.raises(ValueError, match=r"\[forge\] section"):
            read_forge_toml(path)

    def test_legacy_features_table_rejected(self, tmp_path: Path) -> None:
        """Old forge.toml with [forge.features] must error — no silent
        auto-migration."""
        path = self._write(
            tmp_path,
            "\n".join(
                [
                    "[forge]",
                    'version = "0.1.0"',
                    'project_name = "legacy"',
                    "[forge.templates]",
                    'python = "services/python-service-template"',
                    "[forge.features]",
                    'enabled = ["rate_limit"]',
                    "",
                ]
            ),
        )
        with pytest.raises(ValueError, match=r"legacy \[forge\.features\]"):
            read_forge_toml(path)

    def test_legacy_parameters_table_rejected(self, tmp_path: Path) -> None:
        """[forge.parameters] is a pre-Option shape — hard cutover rejects it."""
        path = self._write(
            tmp_path,
            "\n".join(
                [
                    "[forge]",
                    'version = "0.1.0"',
                    'project_name = "legacy"',
                    "[forge.templates]",
                    'python = "p"',
                    "[forge.parameters]",
                    'rag-backend = "qdrant"',
                    "",
                ]
            ),
        )
        with pytest.raises(ValueError, match=r"legacy \[forge\.parameters\]"):
            read_forge_toml(path)

    def test_option_values_unwrap_to_native_types(self, tmp_path: Path) -> None:
        """tomlkit wrapper types are normalized to native Python on read."""
        path = tmp_path / "forge.toml"
        write_forge_toml(
            path,
            version="0.2.0",
            project_name="x",
            templates={"python": "p"},
            options={"middleware.rate_limit": False, "rag.top_k": 7},
        )
        data = read_forge_toml(path)
        assert type(data.options["middleware.rate_limit"]) is bool
        assert type(data.options["rag.top_k"]) is int
        assert data.options["middleware.rate_limit"] is False
        assert data.options["rag.top_k"] == 7


class TestWriterProducesValidToml:
    def test_output_parses_with_tomlkit(self, tmp_path: Path) -> None:
        """Sanity: the writer emits syntactically valid TOML."""
        path = tmp_path / "forge.toml"
        write_forge_toml(
            path,
            version="0.2.0",
            project_name="valid",
            templates={"python": "p", "vue": "v"},
            options={"rag.backend": "qdrant", "middleware.rate_limit": True},
        )
        doc = tomlkit.parse(path.read_text(encoding="utf-8"))
        assert doc["forge"]["project_name"] == "valid"
        assert doc["forge"]["options"]["rag.backend"] == "qdrant"
        assert doc["forge"]["options"]["middleware.rate_limit"] is True


class TestDataclass:
    def test_fields_defaults(self) -> None:
        data = ForgeTomlData(version="0.1", project_name="x")
        assert data.templates == {}
        assert data.options == {}
        # WS2: option_origins is parallel-keyed to options. Empty by
        # default so existing fixtures keep working.
        assert data.option_origins == {}


class TestSchemaV3OptionOrigins:
    """WS2: ``[forge.option_origins]`` table + parallel-keyed origins.

    These tests pin down the round-trip + migration semantics of the
    new v3 schema field. Stage B (resolver + generator + updater) will
    consume the origins; Stage A just wires the plumbing.
    """

    def test_v3_origins_round_trip(self, tmp_path: Path) -> None:
        """Writing + reading a manifest preserves option_origins exactly.

        Initiative #3 bumped CURRENT_SCHEMA_VERSION to 4; the
        ``[forge.option_origins]`` table is still parsed identically,
        so the round-trip semantics carry forward. Schema-version
        assertion pinned to the constant so a future bump only needs
        to update one place.
        """
        path = tmp_path / "forge.toml"
        write_forge_toml(
            path,
            version="0.3.0",
            project_name="acme",
            templates={"python": "services/python-service-template"},
            options={
                "middleware.correlation_id": "always-on",
                "middleware.rate_limit": True,
            },
            option_origins={
                "middleware.correlation_id": "default",
                "middleware.rate_limit": "user",
            },
        )
        data = read_forge_toml(path)
        assert data.schema_version == CURRENT_SCHEMA_VERSION
        assert data.option_origins == {
            "middleware.correlation_id": "default",
            "middleware.rate_limit": "user",
        }
        # Values still land in options unchanged.
        assert data.options == {
            "middleware.correlation_id": "always-on",
            "middleware.rate_limit": True,
        }

    def test_v3_default_schema_version_is_current(self, tmp_path: Path) -> None:
        """Default ``schema_version`` on write tracks CURRENT_SCHEMA_VERSION.

        Initiative #3 bumped the constant from 3 to 4; this test now
        pins the round-trip default to the constant so subsequent bumps
        only require updating CURRENT_SCHEMA_VERSION.
        """
        path = tmp_path / "forge.toml"
        write_forge_toml(
            path,
            version="0.3.0",
            project_name="x",
            templates={"python": "p"},
            options={"middleware.rate_limit": True},
        )
        # The constant moved (was 2 pre-WS2, 3 pre-Initiative-#3, 4 before several frontends).
        assert CURRENT_SCHEMA_VERSION == 5
        data = read_forge_toml(path)
        assert data.schema_version == CURRENT_SCHEMA_VERSION

    def test_write_without_explicit_origins_defaults_to_user(
        self, tmp_path: Path
    ) -> None:
        """Backwards compat: omitting ``option_origins`` stamps every
        option as ``"user"`` — preserves existing call sites' behavior
        until Stage B updates them to pass real origins.
        """
        path = tmp_path / "forge.toml"
        write_forge_toml(
            path,
            version="0.3.0",
            project_name="x",
            templates={"python": "p"},
            options={"middleware.rate_limit": True, "rag.backend": "qdrant"},
            # option_origins intentionally omitted.
        )
        data = read_forge_toml(path)
        assert data.option_origins == {
            "middleware.rate_limit": "user",
            "rag.backend": "user",
        }

    def test_v2_manifest_migrates_to_all_default_origins(
        self, tmp_path: Path
    ) -> None:
        """A hand-crafted v2 manifest reads with origins synthesized as
        all-``"default"`` — see the read-time migration in
        :mod:`forge.sync.manifest`.

        This is the load-bearing safety net: pre-WS2 forge dumped
        resolved defaults into ``[forge.options]`` indistinguishably
        from user-set values. We can't recover user intent post-hoc,
        so we conservatively treat everything as ``"default"`` — that
        way Stage B's resolver silently skips fragments whose backends
        aren't present (instead of erroring on options the user never
        actually asked for).
        """
        path = tmp_path / "forge.toml"
        # Hand-write a minimal v2 manifest — no [forge.option_origins]
        # table. The shape mirrors what pre-WS2 forge produced.
        path.write_text(
            "\n".join(
                [
                    "[forge]",
                    "schema_version = 2",
                    'version = "1.2.0"',
                    'project_name = "demo"',
                    "[forge.templates]",
                    'python = "services/python-service-template"',
                    "[forge.options]",
                    '"middleware.correlation_id" = "always-on"',
                    '"middleware.rate_limit" = true',
                    "",
                ]
            ),
            encoding="utf-8",
        )
        data = read_forge_toml(path)
        # schema_version is REPORTED as found on disk — read does NOT
        # re-stamp. The next generate/update re-stamps to the current
        # schema (v4 as of Initiative #3).
        assert data.schema_version == 2
        # Every persisted option got "default" — we can't recover the
        # user's intent from a v2 file.
        assert data.option_origins == {
            "middleware.correlation_id": "default",
            "middleware.rate_limit": "default",
        }
        # Values themselves are untouched.
        assert data.options == {
            "middleware.correlation_id": "always-on",
            "middleware.rate_limit": True,
        }

    def test_v1_manifest_also_migrates_to_all_default_origins(
        self, tmp_path: Path
    ) -> None:
        """v1 manifests (no ``schema_version`` key) take the same path
        as v2 — origins synthesized as all-``"default"``. The
        ``schema_version < 3`` branch handles both legacies (and v3
        manifests get the same on-disk frontend inference as the v4
        fallback path, but that's a separate test in
        ``tests/test_manifest.py``).
        """
        path = tmp_path / "forge.toml"
        path.write_text(
            "\n".join(
                [
                    "[forge]",
                    'version = "1.1.5"',
                    'project_name = "legacy"',
                    "[forge.templates]",
                    'python = "p"',
                    "[forge.options]",
                    '"rag.top_k" = 7',
                    "",
                ]
            ),
            encoding="utf-8",
        )
        data = read_forge_toml(path)
        assert data.schema_version == 1
        assert data.option_origins == {"rag.top_k": "default"}

    def test_v3_origins_tolerate_partial_writes(self, tmp_path: Path) -> None:
        """A v3 manifest with a partial ``[forge.option_origins]`` table
        falls back to ``"default"`` for missing entries — so a
        hand-edited manifest doesn't blow up the loader.
        """
        path = tmp_path / "forge.toml"
        # Hand-write a v3 manifest where the origins table is missing
        # one of the options the user set. This can happen if a human
        # edits the file directly.
        path.write_text(
            "\n".join(
                [
                    "[forge]",
                    "schema_version = 3",
                    'version = "1.3.0"',
                    'project_name = "demo"',
                    "[forge.templates]",
                    'python = "p"',
                    "[forge.options]",
                    '"middleware.correlation_id" = "always-on"',
                    '"middleware.rate_limit" = true',
                    "[forge.option_origins]",
                    '"middleware.rate_limit" = "user"',
                    "",
                ]
            ),
            encoding="utf-8",
        )
        data = read_forge_toml(path)
        assert data.schema_version == 3
        assert data.option_origins == {
            # Present in the file → as written.
            "middleware.rate_limit": "user",
            # Missing from the file → "default" fallback.
            "middleware.correlation_id": "default",
        }

    def test_v3_origins_for_paths_absent_from_options_are_dropped(
        self, tmp_path: Path
    ) -> None:
        """Origins for paths not present in ``options`` are dropped on
        write — the two tables stay strictly parallel-keyed. Stage B
        relies on the invariant that every origin has a value.
        """
        path = tmp_path / "forge.toml"
        write_forge_toml(
            path,
            version="0.3.0",
            project_name="x",
            templates={"python": "p"},
            options={"rag.backend": "qdrant"},
            option_origins={
                "rag.backend": "user",
                # This origin has no corresponding option — must be
                # dropped, not silently rendered.
                "middleware.rate_limit": "default",
            },
        )
        data = read_forge_toml(path)
        assert data.option_origins == {"rag.backend": "user"}
        # And the orphan key never made it to disk either.
        text = path.read_text(encoding="utf-8")
        assert "middleware.rate_limit" not in text

    def test_origins_sorted_alphabetically_for_diff_stability(
        self, tmp_path: Path
    ) -> None:
        """Like ``[forge.options]``, the origins table is sorted on
        write — keeps diffs minimal across re-stamps.
        """
        path = tmp_path / "forge.toml"
        write_forge_toml(
            path,
            version="0.3.0",
            project_name="x",
            templates={"python": "p"},
            options={"rag.top_k": 5, "middleware.rate_limit": True},
            option_origins={
                "rag.top_k": "user",
                "middleware.rate_limit": "default",
            },
        )
        text = path.read_text(encoding="utf-8")
        # Find the origins table region and confirm alphabetical order.
        origins_start = text.index("[forge.option_origins]")
        origins_section = text[origins_start:]
        rate_limit_pos = origins_section.index('"middleware.rate_limit"')
        top_k_pos = origins_section.index('"rag.top_k"')
        assert rate_limit_pos < top_k_pos
