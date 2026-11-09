import os
from typing import Any, Literal

from pydantic import BaseModel, Field, field_validator, model_validator

from forge_core.domain.config import AuthConfig


class Contact(BaseModel):
    # ``AppConfig.model_dump()`` is splatted into ``FastAPI(**...)`` and
    # FastAPI's OpenAPI ``Contact`` model declares ``email`` as
    # ``Optional[EmailStr]``. An empty string is *not* a valid ``EmailStr``
    # and would crash ``/openapi.json`` with HTTP 500. Coerce blank
    # strings to ``None`` so legacy configs (``contact: {email: ""}``)
    # still work.
    name: str | None = None
    url: str | None = None
    email: str | None = None

    @field_validator("name", "url", "email", mode="before")
    @classmethod
    def _blank_to_none(cls, value: Any) -> Any:
        if isinstance(value, str) and value.strip() == "":
            return None
        return value


class LicenseInfo(BaseModel):
    name: str = "MIT"
    url: str | None = None


class AppConfig(BaseModel):
    title: str = "My Service"
    description: str = "A Python microservice"
    version: str = "0.1.0"
    terms_of_service: str | None = None
    contact: Contact | None = Field(default_factory=Contact)
    license_info: LicenseInfo = LicenseInfo()


class CorsConfig(BaseModel):
    enabled: bool = False
    allow_origins: list[str] = ["*"]
    allow_credentials: bool = True
    allow_methods: list[str] = ["*"]
    allow_headers: list[str] = ["*"]
    max_age: int = 3600

    @model_validator(mode="after")
    def _reject_wildcard_with_credentials_in_prod(self) -> "CorsConfig":
        # Starlette reflects the request Origin (rather than sending ``*``) when
        # allow_origins=["*"] AND allow_credentials=True — i.e. ANY website can
        # make credentialed cross-origin requests. Harmless while CORS is off
        # (the default), but a footgun once enabled. Fail closed in production
        # so a real deploy must name its allowed origins; dev/test keep the
        # permissive default for local cross-origin work.
        if not self.enabled:
            return self
        env = os.getenv("ENV", os.getenv("ENVIRONMENT", "production")).lower()
        if env in ("development", "dev", "local", "test", "testing", "ci"):
            return self
        if self.allow_credentials and "*" in self.allow_origins:
            raise ValueError(
                "CORS allow_origins=['*'] with allow_credentials=True reflects "
                "any origin for credentialed requests — refused in production. "
                "List the exact allowed origins, or set allow_credentials=False."
            )
        return self


class ServerConfig(BaseModel):
    host: str = "0.0.0.0"
    port: int = 5000
    log_level: str = "critical"
    reload: bool = False
    max_workers: int | None = 1
    cors: CorsConfig = CorsConfig()


class DiscoveryConfig(BaseModel):
    enabled: bool = False
    app_name: str = "my-service"
    service_url: str = "http://localhost:8761/eureka"
    service_port: int = 5000
    service_user: str = ""
    service_password: str = ""
    instance_ip: str = ""
    instance_host: str = ""
    instance_port: int = 5000


class SecurityConfig(BaseModel):
    algorithm: str = "HS256"
    secret_key: str = Field("CHANGEME", description="Must be overridden in production")
    access_token_expire_minutes: int = 30
    refresh_token_expire_minutes: int = 60
    auth: AuthConfig

    @model_validator(mode="after")
    def _reject_default_secret_in_prod(self) -> "SecurityConfig":
        env = os.getenv("ENV", os.getenv("ENVIRONMENT", "production"))
        if env.lower() in ("development", "dev", "local", "test", "testing"):
            return self
        # Fail closed: reject blank, the shipped "CHANGEME..." placeholder
        # (the default.yaml ships "CHANGEME-use-a-real-secret-in-production",
        # which an exact "CHANGEME" check silently let through), and any
        # secret too short to be cryptographically sound.
        value = self.secret_key.strip()
        if not value or value.upper().startswith("CHANGEME") or len(value) < 32:
            raise ValueError(
                "security.secret_key is unset or a placeholder — set a strong "
                "random value (>= 32 chars, e.g. "
                "`python -c \"import secrets; print(secrets.token_hex(32))\"`) "
                "before running in production."
            )
        # When auth is enabled (the production.yaml default), the OIDC client
        # secret must also be overridden — default.yaml ships
        # ``client_secret: "changeme"``.
        # Direct field access (not getattr) so a future AuthConfig schema drift
        # fails loudly instead of silently skipping this security check.
        if self.auth.enabled:
            client_secret = (self.auth.client_secret or "").strip()
            if not client_secret or client_secret.upper().startswith("CHANGEME"):
                raise ValueError(
                    "security.auth.client_secret is unset or a placeholder — "
                    "set the real OIDC client secret before running in "
                    "production."
                )
        return self


class DbConfig(BaseModel):
    url: str = Field(
        "sqlite+aiosqlite:///development.db",
        description="Full database connection URL",
    )
    pool_size: int = Field(10, ge=1)
    max_overflow: int = Field(20, ge=0)
    pool_timeout: int = Field(30, ge=1)
    pool_recycle: int = Field(3600, ge=-1)
    echo: bool | Literal["debug"] = Field(False)
    application_name: str = Field("my-service")
    ssl_mode: Literal["disable", "prefer", "require"] | None = None


class HandlerConfig(BaseModel):
    class_: str = Field(..., alias="class")
    level: str | None = None
    formatter: str | None = None
    stream: str | None = None
    filename: str | None = None


class LoggerConfig(BaseModel):
    level: str | None = None
    handlers: list[str]
    propagate: bool | None = None


class LoggingConfig(BaseModel):
    version: int = 1
    formatters: dict[str, dict[str, Any]] = {}
    handlers: dict[str, HandlerConfig] = {}
    loggers: dict[str, LoggerConfig] = {}
    root: LoggerConfig = LoggerConfig(handlers=["console"])


class AuditConfig(BaseModel):
    enabled: bool = Field(True, description="Master switch for audit logging")
    log_request_body: bool = Field(False)
    max_body_size: int = Field(51200)
    excluded_paths: set[str] = {
        "/health",
        "/api/v1/health",
        "/api/v1/healthz",
        "/metrics",
        "/docs",
        "/openapi.json",
        "/favicon.ico",
    }
    excluded_methods: set[str] = {"OPTIONS", "HEAD"}


# Feature fragments (events / streaming / airlock) inject their nested
# config models above this marker, then the matching nested-config field
# below it. Keeping the class-definition anchor above the field anchor
# guarantees a fragment's ``class XSettings(BaseModel)`` is defined before
# the ``x: XSettings = XSettings()`` field that references it.
# FORGE:CONFIG_DOMAIN_FIELDS
# FORGE:CONFIG_DOMAIN_ROOT
# events_outbox extends the events config with relay-tuning fields here.
# FORGE:CONFIG_EVENTS_FIELDS
