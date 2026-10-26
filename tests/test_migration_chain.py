"""Tests for alembic migration rechaining (forge.codegen.migration_chain).

Fragments hard-code revision/down_revision assuming a fixed set; any subset
collides (two 0003s) or gaps (0005 -> missing 0004). rechain_migrations renumbers
the present files into one valid linear history.
"""

from __future__ import annotations

import re
from pathlib import Path

from forge.codegen.migration_chain import rechain_migrations

_REV = re.compile(r'^revision\s*(?::[^=]+)?=\s*(?:"([^"]*)"|None)', re.M)
_DOWN = re.compile(r'^down_revision\s*(?::[^=]+)?=\s*(?:"([^"]*)"|None)', re.M)


def _write(path: Path, *, rev: str, down: str) -> None:
    down_val = "None" if down == "None" else f'"{down}"'
    path.write_text(
        f'"""mig."""\nfrom typing import Union\n'
        f'revision: str = "{rev}"\n'
        f"down_revision: Union[str, None] = {down_val}\n"
        f"def upgrade() -> None: ...\n"
        f"def downgrade() -> None: ...\n",
        encoding="utf-8",
    )


def _rev(p: Path) -> str:
    m = _REV.search(p.read_text(encoding="utf-8"))
    return m.group(1) if m and m.group(1) is not None else "None"


def _down(p: Path) -> str:
    m = _DOWN.search(p.read_text(encoding="utf-8"))
    return m.group(1) if m and m.group(1) is not None else "None"


def test_rechain_fixes_collisions_and_gaps(tmp_path: Path) -> None:
    _write(tmp_path / "0001_initial.py", rev="0001", down="None")
    _write(tmp_path / "0002_conv.py", rev="0002", down="0001")
    _write(tmp_path / "0003_chat.py", rev="0003", down="0002")
    _write(tmp_path / "0003_rag.py", rev="0003", down="0002")  # collision
    _write(tmp_path / "0005_webhooks.py", rev="0005", down="0004")  # gap

    rechain_migrations(tmp_path)

    files = sorted(p for p in tmp_path.glob("*.py") if not p.name.startswith("__"))
    revs = {p.name: _rev(p) for p in files}
    downs = {p.name: _down(p) for p in files}

    # 1. Revisions are unique.
    assert len(set(revs.values())) == len(revs), revs
    # 2. Exactly one root (down_revision None).
    roots = [name for name, d in downs.items() if d == "None"]
    assert roots == ["0001_initial.py"], roots
    # 3. Every non-root down_revision points to a present revision.
    present = set(revs.values())
    for d in downs.values():
        if d != "None":
            assert d in present, d
    # 4. Exactly one head (linear history, no branches).
    targets = {d for d in downs.values() if d != "None"}
    heads = present - targets
    assert len(heads) == 1, heads


def test_rechain_preserves_order_and_is_deterministic(tmp_path: Path) -> None:
    _write(tmp_path / "0001_initial.py", rev="0001", down="None")
    _write(tmp_path / "0002_conv.py", rev="0002", down="0001")
    _write(tmp_path / "0005_webhooks.py", rev="0005", down="0004")
    rechain_migrations(tmp_path)
    # webhooks sorts after conv (5 > 2), so chains off it; base stays root.
    assert _down(tmp_path / "0002_conv.py") == _rev(tmp_path / "0001_initial.py")
    assert _down(tmp_path / "0005_webhooks.py") == _rev(tmp_path / "0002_conv.py")


def test_rechain_noop_on_missing_dir(tmp_path: Path) -> None:
    rechain_migrations(tmp_path / "nope")  # must not raise


def test_rechain_is_idempotent(tmp_path: Path) -> None:
    """Re-running on an already-valid chain is a no-op (the --update path
    rechains every time; it must converge, not churn)."""
    _write(tmp_path / "0001_initial.py", rev="0001", down="None")
    _write(tmp_path / "0002_conv.py", rev="0002", down="0001")
    _write(tmp_path / "0005_webhooks.py", rev="0005", down="0004")
    rechain_migrations(tmp_path)
    second = rechain_migrations(tmp_path)
    assert second == [], "second rechain must touch nothing"


def test_rechain_never_collides_with_skipped_revision(tmp_path: Path) -> None:
    """A non-rewritable file (e.g. a hand-authored merge migration with a tuple
    down_revision) is skipped wholesale, but its revision id must still be
    reserved: renumbering the rewritable files from 0001 must NOT land on a
    revision already held by a skipped file, or alembic rejects the directory
    with 'Multiple revisions present'."""
    _write(tmp_path / "0001_initial.py", rev="0001", down="None")
    # Merge migration: tuple down_revision -> not rewritable, gets skipped.
    # It holds revision "0002"; the naive renumber would also assign "0002"
    # to the next rewritable file below.
    (tmp_path / "0002_merge.py").write_text(
        '"""merge."""\nfrom typing import Union\n'
        'revision: str = "0002"\n'
        'down_revision: Union[str, tuple] = ("0001", "abc")\n'
        "def upgrade() -> None: ...\n"
        "def downgrade() -> None: ...\n",
        encoding="utf-8",
    )
    _write(tmp_path / "0003_chat.py", rev="0003", down="0002")

    rechain_migrations(tmp_path)

    files = sorted(p for p in tmp_path.glob("*.py") if not p.name.startswith("__"))
    revs = [_rev(p) for p in files]
    assert len(set(revs)) == len(revs), f"duplicate revision after rechain: {revs}"


def _assert_valid_chain(versions_dir: Path) -> None:
    files = sorted(p for p in versions_dir.glob("*.py") if not p.name.startswith("__"))
    revs = [_rev(p) for p in files]
    downs = [_down(p) for p in files]
    assert len(set(revs)) == len(revs), f"duplicate revisions: {revs}"
    assert downs.count("None") == 1, f"expected one root, got downs={downs}"
    present = set(revs)
    for d in downs:
        if d != "None":
            assert d in present, f"down_revision {d} missing from {present}"
    heads = present - {d for d in downs if d != "None"}
    assert len(heads) == 1, f"expected one head, got {heads}"


def test_update_preserves_rechained_migrations(tmp_path: Path) -> None:
    """forge --update re-applies the fragments' hard-coded migrations; the
    update path must rechain afterwards or the project regresses to a broken
    (collision/gap) chain that crashes `alembic upgrade head`."""
    from forge.config import BackendConfig, BackendLanguage, ProjectConfig
    from forge.generator import generate
    from forge.sync.forge_to_project.updater import update_project

    opts = {"conversation.persistence": True, "platform.webhooks": True}
    cfg = ProjectConfig(
        project_name="Upd Mig",
        backends=[
            BackendConfig(
                name="api",
                project_name="Upd Mig",
                language=BackendLanguage.PYTHON,
                server_port=8020,
                features=["items"],
            )
        ],
        frontend=None,
        options=opts,
        option_origins={k: "user" for k in opts},
        output_dir=str(tmp_path),
    )
    cfg.validate()
    root = generate(cfg, quiet=True, dry_run=False)
    versions = root / "services" / "api" / "alembic" / "versions"
    _assert_valid_chain(versions)  # fresh generation
    update_project(root, quiet=True)
    _assert_valid_chain(versions)  # MUST still be valid after --update
