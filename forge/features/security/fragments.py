"""Security-posture fragments — SBOM emission.

Distinct from ``middleware.security_headers`` (which sets HTTP response
headers in the request path). ``security_sbom`` emits a
software-bill-of-materials artifact at build time. The ``security.csp``
option needs no fragment: the frontend renderers read it
(:mod:`forge.frontend_csp`).
"""

from __future__ import annotations

from pathlib import Path

from forge.api import ForgeAPI
from forge.config import BackendLanguage
from forge.fragments._spec import Fragment, FragmentImplSpec

_TEMPLATES = Path(__file__).resolve().parent / "templates"


def _impl(name: str, lang: str) -> str:
    return str(_TEMPLATES / name / lang)


def register_all(api: ForgeAPI) -> None:
    api.add_fragment(
        Fragment(
            name="security_sbom",
            implementations={
                BackendLanguage.PYTHON: FragmentImplSpec(
                    fragment_dir=_impl("security_sbom", "python"),
                ),
            },
        )
    )
