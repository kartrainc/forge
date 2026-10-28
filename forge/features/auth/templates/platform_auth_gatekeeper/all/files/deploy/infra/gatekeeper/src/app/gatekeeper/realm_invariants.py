"""Boot-time realm invariant probes.

A recurring auth-chain failure is that the running Keycloak's User-Profile
schema can silently drift from ``deploy/infra/keycloak-realm.json``. When it does,
gatekeeper's ``/callback`` ``set_user_attribute('tenant_id', ...)`` raises only
on the *first* self-registration — every prior boot looked fine.

The ``keycloak-realm-sync`` compose sidecar reconciles the schema before
gatekeeper boots, but that ``depends_on`` guarantee does not apply in
Helm/k8s, where the gatekeeper pod and the sync Job race independently.
This module's job is to verify the invariant on every gatekeeper boot
and refuse to come up otherwise — converting a future user-facing 502
into a loud boot-time crash that surfaces on the deploy dashboard.

With ``TENANT_SOURCE=organization`` it also checks that the realm has
Organizations enabled and the ``gatekeeper`` client its organization mapper.
Keycloak imports the realm file only into an empty database, so an existing
realm upgraded to the organization source keeps neither; without them no
token carries an organization and every sign-in is refused with 403.

The check is **read-only** (GET /admin/realms/{realm}/users/profile, the
realm and its gatekeeper client), so the gatekeeper service can hold admin
credentials without the privilege-creep concern that would attend a *write*
path. The full schema *sync* is intentionally elsewhere.
"""

from __future__ import annotations

import asyncio
import logging
from typing import Any

import httpx

logger = logging.getLogger(__name__)


class RealmInvariantError(RuntimeError):
    """Raised when a required realm invariant doesn't hold.

    Carries the same diagnostic shape as
    :class:`app.gatekeeper.keycloak_admin.GatekeeperKeycloakAdmin.set_user_attribute`'s
    silent-drop message so operators see one consistent error string
    whether the drift surfaces at boot or at first registration.
    """


async def verify_user_profile_active(
    *,
    server_url: str,
    realm: str,
    admin_user: str,
    admin_password: str,
    required_attributes: frozenset[str] = frozenset(["tenant_id"]),
    required_policy: str = "ADMIN_EDIT",
    organizations: bool = False,
    client_id: str = "gatekeeper",
    http_client: httpx.AsyncClient | None = None,
    timeout: float = 10.0,
    max_attempts: int = 5,
    retry_delay: float = 2.0,
) -> None:
    """Read the live User-Profile schema; raise if the invariant fails.

    Invariant:
    - Every name in ``required_attributes`` appears in the live
      ``attributes`` list. Without this the gatekeeper's read-after-write
      on ``set_user_attribute`` silently drops the value.
    - ``unmanagedAttributePolicy`` equals ``required_policy``. Without
      this Keycloak defaults to ``DISABLED``, which drops any attribute
      that isn't explicitly declared — including the ``tenant_id`` that
      legacy users might still need.
    - With ``organizations``, the realm has Organizations enabled and the
      ``client_id`` client an ``oidc-organization-membership-mapper``.

    Raises :class:`RealmInvariantError` on any divergence with the same
    diagnostic the silent-drop guard already produces. Network errors
    (Keycloak unreachable) also raise — the gatekeeper is useless without
    its IdP, so failing fast is the right behavior at boot.
    """
    base = server_url.rstrip("/")
    token_url = f"{base}/realms/master/protocol/openid-connect/token"
    profile_url = f"{base}/admin/realms/{realm}/users/profile"

    owns_client = http_client is None
    client = http_client or httpx.AsyncClient(timeout=timeout)

    async def _probe_once() -> None:
        token_resp = await client.post(
            token_url,
            data={
                "grant_type": "password",
                "username": admin_user,
                "password": admin_password,
                "client_id": "admin-cli",
            },
        )
        if token_resp.status_code != 200:
            raise RealmInvariantError(
                f"realm invariant probe: master admin token grant failed at "
                f"{token_url}: http={token_resp.status_code} body={token_resp.text[:200]!r}. "
                "Check KC_ADMIN_USER / KC_ADMIN_PASSWORD env on the gatekeeper "
                "service (in prod these MUST come from a k8s Secret, not literal env)."
            )
        token = token_resp.json()["access_token"]

        profile_resp = await client.get(
            profile_url,
            headers={"Authorization": f"Bearer {token}"},
        )
        if profile_resp.status_code != 200:
            raise RealmInvariantError(
                f"realm invariant probe: GET {profile_url} failed: "
                f"http={profile_resp.status_code} body={profile_resp.text[:200]!r}."
            )
        profile = profile_resp.json()

        live_attrs = _live_attribute_names(profile)
        missing = required_attributes - live_attrs
        if missing:
            raise RealmInvariantError(
                f"realm invariant probe: User-Profile schema on realm {realm!r} "
                f"is missing required attribute(s) {sorted(missing)!r}. The "
                "gatekeeper's set_user_attribute would silently drop these on "
                "self-registration. Most likely the keycloak-realm-sync sidecar "
                "did not run, or pgdata holds a stale schema. Add the attribute "
                "under components['org.keycloak.userprofile.UserProfileProvider'][0]"
                ".config['kc.user.profile.config'] in deploy/infra/keycloak-realm.json, "
                "or apply directly to a running realm via "
                "PUT /admin/realms/{realm}/users/profile (which is exactly what "
                "the keycloak-realm-sync sidecar does)."
            )

        live_policy = profile.get("unmanagedAttributePolicy")
        if live_policy != required_policy:
            raise RealmInvariantError(
                f"realm invariant probe: unmanagedAttributePolicy on realm "
                f"{realm!r} is {live_policy!r}; expected {required_policy!r}. "
                "With DISABLED, KC silently drops any attribute not declared in "
                "the schema — the original 502 'Tenant assignment failed' "
                "failure mode. Run keycloak-realm-sync (or apply realm.json "
                "via PUT /admin/realms/{realm}/users/profile) to reconcile."
            )
        if organizations:
            await _check_organizations(token)
        logger.info(
            "realm invariant ok: realm=%s attrs=%d policy=%s",
            realm,
            len(profile.get("attributes") or []),
            live_policy,
        )

    async def _check_organizations(token: str) -> None:
        headers = {"Authorization": f"Bearer {token}"}
        realm_resp = await client.get(f"{base}/admin/realms/{realm}", headers=headers)
        realm_resp.raise_for_status()
        upgrade = (
            "An existing realm is not re-imported from deploy/infra/keycloak-realm.json: "
            "apply the Keycloak steps in UPGRADING.md, or set "
            'auth.keycloak_tenant_source="user_attribute" to keep the tenant_id attribute.'
        )
        if not realm_resp.json().get("organizationsEnabled"):
            raise RealmInvariantError(
                f"realm invariant probe: TENANT_SOURCE=organization but realm {realm!r} "
                f"has Organizations disabled, so every sign-in would get 403. {upgrade}"
            )
        clients_resp = await client.get(
            f"{base}/admin/realms/{realm}/clients",
            params={"clientId": client_id},
            headers=headers,
        )
        clients_resp.raise_for_status()
        mappers = [
            mapper.get("protocolMapper")
            for found in clients_resp.json()
            for mapper in found.get("protocolMappers") or []
        ]
        if "oidc-organization-membership-mapper" not in mappers:
            raise RealmInvariantError(
                f"realm invariant probe: TENANT_SOURCE=organization but the {client_id!r} "
                f"client of realm {realm!r} has no organization membership mapper, so no "
                f"token names an organization and every sign-in would get 403. {upgrade}"
            )

    try:
        for attempt in range(1, max_attempts + 1):
            try:
                await _probe_once()
                return
            except (RealmInvariantError, httpx.HTTPError) as exc:
                # During boot the realm may still be importing — Keycloak reports
                # "healthy" on a TCP check before its User-Profile schema and
                # admin endpoints are ready — so the token grant, the profile
                # GET, or the attribute/policy can all be *transiently* absent.
                # Retry with backoff so a slow IdP doesn't crash-loop the
                # gatekeeper; a genuine, persistent drift still fails once the
                # attempt budget is exhausted.
                if attempt >= max_attempts:
                    raise
                logger.warning(
                    "realm invariant probe attempt %d/%d failed (%s); retrying in %.1fs",
                    attempt,
                    max_attempts,
                    exc,
                    retry_delay,
                )
                await asyncio.sleep(retry_delay)
    finally:
        if owns_client:
            await client.aclose()


def _live_attribute_names(profile: dict[str, Any]) -> set[str]:
    return {
        attr.get("name", "")
        for attr in profile.get("attributes") or []
        if isinstance(attr, dict) and attr.get("name")
    }
