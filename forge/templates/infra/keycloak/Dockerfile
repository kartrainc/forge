FROM quay.io/keycloak/keycloak:26.6 AS builder

# Bake custom VS Code theme into the image
COPY themes/vscode /opt/keycloak/themes/vscode

# Pre-build optimized Keycloak (caches themes, compiles templates)
RUN /opt/keycloak/bin/kc.sh build

FROM quay.io/keycloak/keycloak:26.6

COPY --from=builder /opt/keycloak/ /opt/keycloak/

ENTRYPOINT ["/opt/keycloak/bin/kc.sh"]
