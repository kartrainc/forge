"""A forge project has a list of frontends — the config, the builder and the recipe.

``ProjectConfig.frontends`` mirrors ``backends``: a list whose first entry is the
primary, with ``frontend`` a property over it the way ``backend`` is. The stack
file accepts ``frontend:`` (one table, as before) and ``frontends:`` (a list).
"""

from __future__ import annotations

import dataclasses
import json
from argparse import Namespace

import pytest

from forge.cli.builder import _build_config
from forge.cli.parser import _build_parser
from forge.config import (
    PRIMARY_FRONTEND_NAME,
    BackendConfig,
    FrontendConfig,
    FrontendFramework,
    ProjectConfig,
)
from forge.quality.model import RECIPE, read_recipe, write_recipe


def _args(**overrides: object) -> Namespace:
    ns = _build_parser().parse_args([])
    for key, value in overrides.items():
        setattr(ns, key, value)
    return ns


def _vue(name: str = PRIMARY_FRONTEND_NAME, **kw: object) -> FrontendConfig:
    return FrontendConfig(framework=FrontendFramework.VUE, project_name="Test", name=name, **kw)  # type: ignore[arg-type]


def _flutter(name: str = PRIMARY_FRONTEND_NAME, **kw: object) -> FrontendConfig:
    # Flutter requires include_openapi — see FrontendConfig.validate.
    return FrontendConfig(
        framework=FrontendFramework.FLUTTER,
        project_name="Test",
        include_openapi=True,
        name=name,
        **kw,  # type: ignore[arg-type]
    )


def _project(
    *frontends: FrontendConfig, backends: list[BackendConfig] | None = None
) -> ProjectConfig:
    if backends is None:
        backends = [BackendConfig(project_name="Test", name="api")]
    return ProjectConfig(project_name="Test", backends=backends, frontends=list(frontends))


class TestConfigShape:
    def test_frontends_is_the_field_and_frontend_the_primary(self):
        first, second = _vue(), _vue("admin", server_port=5174)
        cfg = _project(first, second)
        assert cfg.frontends == [first, second]
        assert cfg.frontend is first
        names = {f.name for f in dataclasses.fields(cfg)}
        assert "frontends" in names and "frontend" not in names
        assert "frontends" in dataclasses.asdict(cfg) and "frontend" not in dataclasses.asdict(cfg)

    def test_frontend_kwarg_is_gone_like_backend(self):
        with pytest.raises(TypeError):
            ProjectConfig(project_name="Test", frontend=_vue())  # type: ignore[call-arg]
        with pytest.raises(TypeError):
            ProjectConfig(project_name="Test", backend=BackendConfig(project_name="Test"))  # type: ignore[call-arg]

    def test_frontend_setter_mirrors_backend(self):
        cfg = _project()
        assert cfg.frontend is None
        first = _vue()
        cfg.frontend = first
        assert cfg.frontends == [first]
        replacement = _vue("web")
        cfg.frontend = replacement
        assert cfg.frontends == [replacement]
        cfg.frontends.append(_vue("admin", server_port=5174))
        cfg.frontend = first
        assert cfg.frontends[0] is first and len(cfg.frontends) == 2
        cfg.frontend = None
        assert cfg.frontends == []

    def test_frontend_slug_is_the_primary_name(self):
        assert _project().frontend_slug == "frontend"
        assert _project(_vue("web")).frontend_slug == "web"
        assert _project(_vue(), _vue("admin")).frontend_slug == "frontend"

    def test_active_frontends_hosts_and_labels(self):
        none = FrontendConfig(
            framework=FrontendFramework.NONE, project_name="Test", include_auth=False
        )
        primary, admin, mobile = _vue(), _vue("admin", server_port=5174), _flutter("frontend_2")
        cfg = _project(primary, admin, mobile, none)
        assert cfg.active_frontends == [primary, admin, mobile]
        assert cfg.is_primary_frontend(primary) and not cfg.is_primary_frontend(admin)
        assert cfg.frontend_host(primary) == "app.localhost"
        assert cfg.frontend_host(admin) == "admin.localhost"
        assert mobile.dns_name == "frontend-2"
        assert cfg.frontend_host(mobile) == "frontend-2.localhost"
        assert cfg.frontend_host_labels == ["admin", "frontend-2"]

    def test_page_features(self):
        assert _vue().page_features(["items", "orders"]) == ["items", "orders"]
        assert _vue(features=[]).page_features(["items"]) == []
        assert _vue(features=["orders"]).page_features(["items", "orders"]) == ["orders"]


class TestBuilder:
    def test_single_table_is_a_list_of_one(self):
        cfg = _build_config(_args(), {"project_name": "demo", "frontend": {"framework": "vue"}})
        assert [fc.name for fc in cfg.frontends] == [PRIMARY_FRONTEND_NAME]
        assert cfg.frontend is cfg.frontends[0]
        assert cfg.frontend.features is None  # every backend feature, as always
        assert cfg.frontend_slug == "frontend"

    @pytest.mark.parametrize(
        ("table", "expected"),
        [
            ({"framework": "vue"}, None),
            ({"framework": "vue", "features": None}, None),  # a bare `features:` is unset
            ({"framework": "vue", "features": []}, []),
            ({"framework": "vue", "features": ["items"]}, ["items"]),
        ],
    )
    def test_single_table_reads_features(self, table, expected):
        cfg = _build_config(_args(), {"project_name": "demo", "frontend": table})
        assert cfg.frontend.features == expected

    def test_list_entry_bare_features_is_unset(self):
        cfg = _build_config(
            _args(), {"project_name": "demo", "frontends": [{"framework": "vue", "features": None}]}
        )
        assert cfg.frontend.features is None

    @pytest.mark.parametrize("key", ["frontends", "backends"])
    @pytest.mark.parametrize("value", [{"framework": "vue", "name": "api"}, "vue"])
    def test_list_key_must_be_a_list(self, key, value):
        with pytest.raises(ValueError, match=f"'{key}:' must be a list of tables"):
            _build_config(_args(), {"project_name": "demo", key: value})

    def test_list_entry_must_be_a_table(self):
        with pytest.raises(ValueError, match=r"frontends\[0\] must be a table"):
            _build_config(
                _args(), {"project_name": "demo", "frontends": ["junk", {"framework": "vue"}]}
            )

    def test_list_entry_empty_name_is_not_replaced_by_the_default(self):
        cfg = _build_config(
            _args(), {"project_name": "demo", "frontends": [{"framework": "vue", "name": ""}]}
        )
        assert cfg.frontend.name == ""
        with pytest.raises(ValueError, match="DNS label"):
            cfg.validate()

    @pytest.mark.parametrize(
        ("auths", "keycloak"), [((True, False), True), ((False, False), False)]
    )
    def test_include_auth_follows_any_entry(self, auths, keycloak):
        cfg = _build_config(
            _args(),
            {
                "project_name": "demo",
                "frontends": [
                    {"framework": "vue", "include_auth": auths[0]},
                    {"framework": "vue", "name": "admin", "include_auth": auths[1]},
                ],
            },
        )
        assert [fc.include_auth for fc in cfg.frontends] == list(auths)
        assert cfg.include_keycloak is keycloak

    def test_preset_with_an_empty_user_list(self):
        cfg = _build_config(_args(platform="monolithic"), {"project_name": "demo", "frontends": []})
        assert cfg.frontends == []

    def test_list_of_one_two_and_three(self):
        one = _build_config(_args(), {"project_name": "demo", "frontends": [{"framework": "vue"}]})
        assert [fc.name for fc in one.frontends] == ["frontend"]
        assert one.frontend.server_port == 5173

        two = _build_config(
            _args(),
            {
                "project_name": "demo",
                "frontends": [
                    {"framework": "vue"},
                    {"framework": "vue", "name": "admin", "ui_kit": "nuxt-ui", "features": []},
                ],
            },
        )
        assert [fc.name for fc in two.frontends] == ["frontend", "admin"]
        assert [fc.server_port for fc in two.frontends] == [5173, 5174]
        assert [fc.ui_kit for fc in two.frontends] == ["shadcn", "nuxt-ui"]
        assert [fc.features for fc in two.frontends] == [None, []]

        three = _build_config(
            _args(),
            {
                "project_name": "demo",
                "frontends": [
                    {"framework": "vue"},
                    {"framework": "svelte", "features": ["items"]},
                    {"framework": "flutter", "include_openapi": True},
                ],
            },
        )
        assert [fc.name for fc in three.frontends] == ["frontend", "frontend-1", "frontend_2"]
        assert [fc.dns_name for fc in three.frontends] == ["frontend", "frontend-1", "frontend-2"]
        assert [fc.server_port for fc in three.frontends] == [5173, 5174, 5175]
        assert three.frontends[1].features == ["items"]

    def test_list_entry_needs_a_framework(self):
        with pytest.raises(ValueError, match=r"frontends\[1\] needs a framework"):
            _build_config(
                _args(),
                {"project_name": "demo", "frontends": [{"framework": "vue"}, {"name": "admin"}]},
            )

    def test_both_keys_refused(self):
        with pytest.raises(ValueError, match="not both"):
            _build_config(
                _args(),
                {
                    "project_name": "demo",
                    "frontend": {"framework": "vue"},
                    "frontends": [{"framework": "vue"}],
                },
            )

    def test_preset_frontend_merges_under_the_primary(self):
        cfg = _build_config(
            _args(platform="monolithic"),
            {
                "project_name": "demo",
                "frontends": [
                    {"framework": "vue", "include_chat": True},
                    {"framework": "vue", "name": "admin"},
                ],
            },
        )
        assert cfg.platform_template == "monolithic"
        assert [fc.name for fc in cfg.frontends] == ["frontend", "admin"]
        assert cfg.frontend.include_chat is True
        assert cfg.frontend.layout == "sidebar"  # the preset's value, merged under the user's entry

    def test_keycloak_settings_reach_every_frontend(self):
        cfg = _build_config(
            _args(),
            {
                "project_name": "demo",
                "include_keycloak": True,
                "keycloak": {"realm": "demo-realm", "client_id": "demo-client"},
                "frontends": [{"framework": "vue"}, {"framework": "vue", "name": "admin"}],
            },
        )
        assert [fc.keycloak_realm for fc in cfg.frontends] == ["demo-realm", "demo-realm"]
        assert [fc.keycloak_client_id for fc in cfg.frontends] == ["demo-client", "demo-client"]

    def test_frontend_flags_refused_beside_a_list(self):
        with pytest.raises(
            ValueError, match="--frontend-port cannot be combined with a 'frontends:' list"
        ):
            _build_config(
                _args(frontend_port=5999),
                {"project_name": "demo", "frontends": [{"framework": "vue"}]},
            )
        with pytest.raises(ValueError, match="--include-auth"):
            _build_config(
                _args(include_auth=False),
                {"project_name": "demo", "frontends": [{"framework": "vue"}]},
            )

    def test_backend_flags_refused_beside_a_list(self):
        with pytest.raises(
            ValueError, match="--backend-port cannot be combined with a 'backends:' list"
        ):
            _build_config(
                _args(backend_port=5001), {"project_name": "demo", "backends": [{"name": "api"}]}
            )

    def test_backend_flags_beside_a_preset_name_the_preset(self):
        # A --platform preset brings its own backends: list.
        with pytest.raises(ValueError, match="or the --platform preset"):
            _build_config(_args(platform="monolithic", features="items,orders"), {})

    def test_the_single_table_and_a_list_of_one_name_the_app_alike(self):
        for cfg in (
            {"project_name": "demo", "frontend": {"framework": "vue", "name": "web"}},
            {"project_name": "demo", "frontends": [{"framework": "vue", "name": "web"}]},
        ):
            frontend = _build_config(_args(), cfg).frontend
            assert frontend.name == "web"
        default = _build_config(_args(), {"project_name": "demo", "frontend": {"framework": "vue"}})
        assert default.frontend.name == "frontend"

    def test_flags_still_override_the_single_table(self):
        cfg = _build_config(
            _args(frontend_port=5999), {"project_name": "demo", "frontend": {"framework": "vue"}}
        )
        assert cfg.frontend.server_port == 5999


class TestValidation:
    def test_two_frontends_validate(self):
        _project(_vue(), _vue("admin", server_port=5174, features=[])).validate()

    def test_unnamed_secondary_flutter_default(self):
        cfg = _project(_vue(), _flutter("frontend_1"))
        cfg.validate()
        assert cfg.frontends[1].dns_name == "frontend-1"

    @pytest.mark.parametrize(
        ("frontends", "message"),
        [
            ((_vue(), _vue("frontend", server_port=5174)), "must be unique"),
            (
                (_vue(), _vue("admin", server_port=5173)),
                "Port 5173 is used by both admin and frontend",
            ),
            ((_vue(), _vue("api", server_port=5174)), "collides with backend 'api'"),
            ((_vue(), _vue("e2e", server_port=5174)), "reserved"),
            ((_vue(), _vue("api-migrate", server_port=5174)), "migrate"),
            ((_vue(), _flutter("api_migrate")), "migrate"),
            ((_vue(), _vue("Admin", server_port=5174)), "DNS label"),
            ((_vue(), _flutter("staff-ui")), "snake_case"),
            ((_vue(), _vue("staff_ui", server_port=5174)), "DNS label"),
            (
                (_vue(), _vue("staff-ui", server_port=5174), _flutter("staff_ui")),
                "derive the same host name",
            ),
            ((_vue(), _vue("app", server_port=5174)), "primary frontend's host label"),
            ((_vue(), _vue("admin", server_port=5174, features=["orders"])), "no backend declares"),
            ((_vue(), _vue("admin-", server_port=5174)), "DNS label"),
            ((_vue(), _flutter("admin_")), "snake_case"),
            (
                (_vue(), _vue("admin", server_port=5174), _vue("portal", server_port=5174)),
                "Port 5174 is used by both portal and admin",
            ),
            (
                (
                    FrontendConfig(
                        framework=FrontendFramework.NONE, project_name="Test", include_auth=False
                    ),
                    _vue("admin", server_port=5174),
                ),
                "can only be the project's only frontend",
            ),
        ],
    )
    def test_refusals(self, frontends, message):
        with pytest.raises(ValueError, match=message):
            _project(*frontends).validate()

    def test_flutter_secondary_port_is_exempt(self):
        _project(_vue(), _flutter("mobile", server_port=5173)).validate()

    def test_pages_are_not_checked_without_backends(self):
        cfg = ProjectConfig(
            project_name="Test",
            backends=[],
            frontends=[_vue(features=["anything"])],
            options={"backend.mode": "none", "frontend.api_target.url": "https://api.example.com"},
        )
        cfg.validate()

    def test_primary_is_found_by_name_not_identity(self):
        cfg = _project(_vue(), _vue("admin", server_port=5174))
        copy = dataclasses.replace(cfg.frontend)
        assert cfg.is_primary_frontend(copy)
        assert cfg.frontend_host(copy) == "app.localhost"

    def test_primary_port_error_text_unchanged(self):
        cfg = _project(_vue(server_port=5000))
        with pytest.raises(ValueError, match="Port 5000 is used by both frontend and api"):
            cfg.validate()

    def test_all_features_tops_up_from_every_frontend(self):
        cfg = ProjectConfig(
            project_name="Test",
            backends=[],
            frontends=[_vue(features=["a"]), _vue("admin", server_port=5174, features=["b", "a"])],
        )
        assert cfg.all_features == ["a", "b"]
        with_backend = _project(
            _vue(features=["items"]), _vue("admin", server_port=5174, features=[])
        )
        assert with_backend.all_features == ["items"]


class TestRecipe:
    @pytest.mark.parametrize("count", [0, 1, 2, 3])
    def test_round_trip(self, tmp_path, count):
        frontends = [
            _vue(),
            _vue("admin", server_port=5174, features=[]),
            _vue("portal", server_port=5175, features=["items"]),
        ][:count]
        write_recipe(tmp_path, _project(*frontends))
        data = json.loads((tmp_path / RECIPE).read_text(encoding="utf-8"))["config"]
        assert [fc["name"] for fc in data["frontends"]] == [fc.name for fc in frontends]
        assert data["frontend"] == (data["frontends"][0] if frontends else None)
        back = read_recipe(tmp_path, check_generator=False)
        assert [fc.name for fc in back.frontends] == [fc.name for fc in frontends]
        assert [fc.features for fc in back.frontends] == [fc.features for fc in frontends]

    def test_none_framework_is_not_recorded(self, tmp_path):
        none = FrontendConfig(
            framework=FrontendFramework.NONE, project_name="Test", include_auth=False
        )
        write_recipe(tmp_path, _project(none))
        data = json.loads((tmp_path / RECIPE).read_text(encoding="utf-8"))["config"]
        assert data["frontend"] is None and data["frontends"] == []

    def test_pre_list_recipe_reads_features_as_all(self, tmp_path):
        write_recipe(tmp_path, _project(_vue()))
        path = tmp_path / RECIPE
        payload = json.loads(path.read_text(encoding="utf-8"))
        payload["schema_version"] = 1
        del payload["config"]["frontends"]
        payload["config"]["frontend"]["features"] = []  # the old dataclass default
        del payload["config"]["frontend"]["name"]
        path.write_text(json.dumps(payload), encoding="utf-8")
        back = read_recipe(tmp_path, check_generator=False)
        assert [fc.name for fc in back.frontends] == ["frontend"]
        assert back.frontend.features is None

    def test_the_recipe_is_version_2_and_keeps_an_empty_page_list(self, tmp_path):
        write_recipe(tmp_path, _project(_vue(features=[])))
        payload = json.loads((tmp_path / RECIPE).read_text(encoding="utf-8"))
        assert payload["schema_version"] == 2
        assert read_recipe(tmp_path, check_generator=False).frontend.features == []

    def test_the_primary_is_read_from_frontend_and_the_others_from_the_list(self, tmp_path):
        write_recipe(tmp_path, _project(_vue(), _vue("admin", server_port=5174)))
        path = tmp_path / RECIPE
        payload = json.loads(path.read_text(encoding="utf-8"))
        # The documented way to switch the primary's kit edits ``frontend``.
        payload["config"]["frontend"]["ui_kit"] = "nuxt-ui"
        payload["config"]["frontends"][1]["ui_kit"] = "nuxt-ui"
        path.write_text(json.dumps(payload), encoding="utf-8")
        back = read_recipe(tmp_path, check_generator=False)
        assert [(fc.name, fc.ui_kit) for fc in back.frontends] == [
            ("frontend", "nuxt-ui"),
            ("admin", "nuxt-ui"),
        ]

    def test_an_unknown_recipe_version_is_refused(self, tmp_path):
        write_recipe(tmp_path, _project(_vue()))
        path = tmp_path / RECIPE
        payload = json.loads(path.read_text(encoding="utf-8"))
        payload["schema_version"] = 3
        path.write_text(json.dumps(payload), encoding="utf-8")
        with pytest.raises(ValueError, match="Unsupported quality recipe version"):
            read_recipe(tmp_path, check_generator=False)
