"""Vendor the official Nuxt UI agent skill at a release tag.

Fetches ``skills/nuxt-ui`` and the repository's ``LICENSE.md`` from
github.com/nuxt/ui at ``TAG`` through the GitHub CLI (``gh``), checks every
file against the Git blob hash GitHub lists for it, replaces
``forge/templates/_common/skills/nuxt-ui``, records the hashes in
``nuxt-ui.upstream.json`` beside it, and sets ``NUXT_UI_SKILL_TAG`` and
``NUXT_UI_SKILL_COMMIT`` in ``forge/ui_kits.py``. Run it when
``NUXT_UI_VERSION`` moves, then read the diff before committing:

    uv run python scripts/vendor_nuxt_ui_skill.py v4.13.0
"""

from __future__ import annotations

import argparse
import base64
import hashlib
import json
import re
import shutil
import subprocess
import tempfile
from pathlib import Path
from typing import Any

REPOSITORY = "nuxt/ui"
ROOT = Path(__file__).resolve().parents[1]
SKILL = ROOT / "forge" / "templates" / "_common" / "skills" / "nuxt-ui"
MANIFEST = SKILL.with_name("nuxt-ui.upstream.json")
UI_KITS = ROOT / "forge" / "ui_kits.py"
SOURCE_DIR = "skills/nuxt-ui/"
LICENCE = "LICENSE.md"


def git_blob_sha(data: bytes) -> str:
    """The hash Git, and GitHub's tree listing, give a file's content."""
    return hashlib.sha1(b"blob %d\0" % len(data) + data).hexdigest()


def gh(path: str) -> Any:
    output = subprocess.run(["gh", "api", path], check=True, capture_output=True, text=True).stdout
    return json.loads(output)


def vendor(tag: str) -> dict:
    commit = gh(f"repos/{REPOSITORY}/commits/{tag}")["sha"]
    tree = gh(f"repos/{REPOSITORY}/git/trees/{commit}?recursive=1")
    wanted = {
        entry["path"]: entry["sha"]
        for entry in tree["tree"]
        if entry["type"] == "blob"
        and (entry["path"].startswith(SOURCE_DIR) or entry["path"] == LICENCE)
    }
    if LICENCE not in wanted or f"{SOURCE_DIR}SKILL.md" not in wanted:
        raise SystemExit(f"{tag} has no {SOURCE_DIR}SKILL.md or {LICENCE}")
    files: dict[str, str] = {}
    with tempfile.TemporaryDirectory() as staging:
        for path, sha in sorted(wanted.items()):
            data = base64.b64decode(gh(f"repos/{REPOSITORY}/git/blobs/{sha}")["content"])
            if git_blob_sha(data) != sha:
                raise SystemExit(f"{path}: content does not match blob {sha}")
            rel = LICENCE if path == LICENCE else path.removeprefix(SOURCE_DIR)
            target = Path(staging) / rel
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_bytes(data)
            files[rel] = sha
        shutil.rmtree(SKILL, ignore_errors=True)
        shutil.copytree(staging, SKILL)
    record = {
        "repository": f"https://github.com/{REPOSITORY}",
        "tag": tag,
        "commit": commit,
        "source": f"{SOURCE_DIR.rstrip('/')} and {LICENCE} (the repository's licence)",
        "files": dict(sorted(files.items())),
    }
    MANIFEST.write_text(json.dumps(record, indent=2) + "\n", encoding="utf-8")
    text = UI_KITS.read_text(encoding="utf-8")
    text = re.sub(r'NUXT_UI_SKILL_TAG = "[^"]*"', f'NUXT_UI_SKILL_TAG = "{tag}"', text)
    text = re.sub(r'NUXT_UI_SKILL_COMMIT = "[^"]*"', f'NUXT_UI_SKILL_COMMIT = "{commit}"', text)
    UI_KITS.write_text(text, encoding="utf-8")
    return record


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("tag", help="the @nuxt/ui release tag, e.g. v4.12.0")
    record = vendor(parser.parse_args().tag)
    print(f"Vendored {len(record['files'])} files at {record['tag']} ({record['commit']}).")
