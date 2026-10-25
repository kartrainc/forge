"""Reference forge plugin — adds a **Go stdlib** backend language.

This is the companion to ``forge-plugin-example`` (which demonstrates the
option → fragment surface). Here we exercise the *backend* surface of the
plugin SDK: :meth:`forge.api.ForgeAPI.add_backend` registers a brand-new
backend **language** (``go-stdlib``) so ``forge --backend-language go-stdlib``
generates a real, compiling service without forking forge.

Go is also a built-in backend (``go``). This plugin registers a separate,
dependency-free variant under its own language value: the standard library
alone makes a useful HTTP service (no dependency resolution to mock), and
`go build` gives a crisp end-to-end "does it compile" signal for the
plugin-backend e2e, which exercises the language-agnostic generator paths.

The Go service template ships **inside this package** (``go-service-template/``)
and is handed to forge as an absolute path — built-in forge templates live
under ``forge/templates/services/`` and are referenced by relative path, but a
plugin can't assume where its package lands on disk, so it resolves the path
from ``__file__``. ``forge.generator`` joins template dirs with its own
templates root via ``pathlib`` (``root / "/abs/path" == "/abs/path"``), so an
absolute template dir renders correctly.

``add_backend`` also seeds the default ``crud-service`` application-template
variant for the new language, so a ``BackendConfig(language=go)`` validates
and generates out of the box.
"""

from __future__ import annotations

from pathlib import Path

from forge.api import ForgeAPI
from forge.config import BackendSpec
from forge_go_backend.toolchain import GO_TOOLCHAIN

_PACKAGE_ROOT = Path(__file__).resolve().parent
_TEMPLATE_DIR = _PACKAGE_ROOT / "go-service-template"


def register(api: ForgeAPI) -> None:
    api.add_backend(
        "go-stdlib",
        BackendSpec(
            template_dir=str(_TEMPLATE_DIR),
            display_label="Go stdlib (reference plugin)",
            version_field="go_version",
            version_choices=("1.27", "1.26", "1.25"),
            toolchain=GO_TOOLCHAIN,
            version="1.0.0",
        ),
    )
