FROM quay.io/keycloak/keycloak:26.4.5

# Bumped from 25.0.6 -> 26.4.5 to match the SPI version the VNPT CAS
# provider jar below was built against (keycloak-cas-26.4.5-vnpt-*.jar) —
# see docker-compose.yml's keycloak service for the same note. The jar is
# baked into the image rather than mounted (unlike docker-compose, which
# bind-mounts it straight from this directory) because it's a 1.3MB
# binary — too big for a ConfigMap (1MiB limit) — and this repo's other
# custom k8s images (mlflow, airflow) already bake in whatever the stock
# image is missing rather than fight volume mounts for it.
COPY keycloak-cas-26.4.5-vnpt-1.1.6.jar /opt/keycloak/providers/keycloak-cas-26.4.5-vnpt-1.1.6.jar

# realm-idma.json is NOT baked in here — it's mounted from a ConfigMap
# (see 00-configmap-keycloak-realm.yaml) instead, the same "config stays
# in k8s manifests, not the image" split used by every other service in
# this repo (e.g. postgres's init scripts, trino's catalogs).
