"""Where a Go ``MiddlewareSpec`` lands relative to the built-in Go middleware.

``render_chi_middleware`` registers after ``FORGE:MIDDLEWARE_REGISTRATION``
while the built-in Go fragments' ``inject.yaml`` files mostly register before
it, so a spec's ``order`` only ranks it among the entries after the marker.
"""

from __future__ import annotations

from pathlib import Path

from forge.appliers.injection import _apply_zoned_injection
from forge.appliers.plan import _Injection, _load_injections
from forge.config import BACKEND_REGISTRY, BackendLanguage
from forge.fragments import FRAGMENT_REGISTRY
from forge.generator import TEMPLATES_DIR
from forge.middleware_spec import MiddlewareSpec, render_middleware_injections

_FEATURES = Path(__file__).resolve().parent.parent / "forge" / "features"


def _builtin(fragment: str) -> tuple[int, str, list[_Injection]]:
    inject = _FEATURES / "middleware" / "templates" / fragment / "go" / "inject.yaml"
    return FRAGMENT_REGISTRY[fragment].order, fragment, _load_injections(inject, fragment)


def _spec(name: str, order: int) -> tuple[int, str, tuple[_Injection, ...]]:
    spec = MiddlewareSpec(
        name=name,
        backend=BackendLanguage.GO,
        order=order,
        import_snippet="",
        register_snippet=f"middleware.{name.title().replace('_', '')},",
    )
    return order, name, render_middleware_injections((spec,), BackendLanguage.GO, name)


def test_builtin_before_fragments_wrap_every_middleware_spec(tmp_path: Path) -> None:
    template = (
        TEMPLATES_DIR
        / BACKEND_REGISTRY[BackendLanguage.GO].template_dir
        / "template"
        / "internal"
        / "app"
        / "app.go.jinja"
    )
    app_go = tmp_path / "app.go"
    app_go.write_text(template.read_text(encoding="utf-8"), encoding="utf-8")
    fragments = [
        _builtin("security_headers"),
        _builtin("rate_limit"),
        _spec("inner_spec", 40),
        _spec("outer_spec", 90),
    ]
    assert [inj.position for inj in _builtin("security_headers")[2]] == ["before"]
    assert [inj.position for inj in _builtin("rate_limit")[2]] == ["after"]

    for _order, _name, injections in sorted(fragments, key=lambda f: (f[0], f[1])):
        for injection in injections:
            _apply_zoned_injection(app_go, injection)

    lines = [line.strip() for line in app_go.read_text(encoding="utf-8").splitlines()]
    positions = [
        next(i for i, line in enumerate(lines) if line.startswith(entry))
        for entry in (
            "middleware.CORS(",
            "middleware.SecurityHeaders,",
            "// FORGE:MIDDLEWARE_REGISTRATION",
            "middleware.OuterSpec,",
            "middleware.RateLimit(",
            "middleware.InnerSpec,",
            "devIdentity,",
        )
    ]
    assert positions == sorted(positions), lines
