# forge-go-backend

A **reference forge plugin** that adds a brand-new backend **language** — a
dependency-free Go variant, `go-stdlib` — through the public plugin SDK. Go is
also a built-in backend (`go`); this plugin registers its own language value so
it exercises the `add_backend` surface without colliding with it. It's the backend-surface companion
to [`forge-plugin-example`](../forge-plugin-example) (which demonstrates the
option → fragment surface).

## What it shows

- `forge.api.ForgeAPI.add_backend(language_value, spec)` registering a new
  language so `forge --backend-language go-stdlib` generates a real, compiling
  service — no fork of forge required.
- Shipping a **template tree inside the plugin package** and handing it to
  forge as an absolute `template_dir` (built-in templates use relative paths;
  a plugin can't assume its install location, so it resolves from `__file__`).
- A `BackendToolchain` implementation (`toolchain.py`) wiring `go build` /
  `go vet` into forge's `verify` phase and `gofmt -w` into `post_generate`.

## Install & use

```bash
pip install -e examples/forge-go-backend          # editable, for local dev
forge --backend-language go-stdlib --backend-name api --output-dir ./my-go-app
cd my-go-app/services/api && go test ./... && go run .
```

`add_backend` also seeds the default `crud-service` application-template
variant for `go-stdlib`, so a `BackendConfig(language=go-stdlib)` validates and generates
out of the box.

## Layout

```
src/forge_go_backend/
  __init__.py            register(api) → api.add_backend("go-stdlib", ...)
  toolchain.py           GoToolchain: install / verify / post_generate
  go-service-template/   the Copier template forge renders
    copier.yml
    _forge_template.toml
    template/
      main.go.jinja
      main_test.go.jinja
      go.mod.jinja
      Dockerfile.jinja
      docker-compose.fragment.yaml.jinja
      README.md.jinja
      .env.example
      .gitignore
```
