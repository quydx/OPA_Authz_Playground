"""The backend's connection to the Feast feature server — a client only,
same shape as mlflow_client.py/airflow_client.py: plain functions, no
class, one env var (FEAST_FEATURE_SERVER_URL) is the only thing that needs
to be right. Plain urllib (not `requests`, which isn't a dependency here)
over the feature server's own REST API — never the Feast SDK, same
"touch it over HTTP, not the library" choice automl_wizard_pipeline.py's
luu_tru_dac_trung task makes for the same service, and the same reason
registry writes live in idma-feast-admin instead (see
poc/feast/registry_api.py) rather than here.

Demo-loop-closing only: lets the UI show "here's what's actually stored
for this entity" after a real run, which /api/wizard/run/{id} on its own
can't (that endpoint reports the *write*, not a *read back*). Nothing
else in this backend depends on this module.

No hardcoded feature view/feature list any more — every feature view is
now user-named and dynamically-shaped (see registry_api.py's
create-or-update-by-name endpoint), so the caller has to say which view
and which of its features it wants, same as Feast's own
/get-online-features API itself requires.
"""
import json
import os
import urllib.error
import urllib.request

FEAST_FEATURE_SERVER_URL = os.environ.get("FEAST_FEATURE_SERVER_URL", "http://feast:6566")


class FeastClientError(Exception):
    pass


def get_online_features(entity_id: int, feature_view: str, features: list[str]) -> dict | None:
    """The latest materialized values of `features` (plain names, not
    "view:feature" — this adds the feature_view prefix itself) for one
    entity_id, straight from Feast's online store. None if that entity
    has never been materialized for this feature view — Feast itself
    doesn't error on an unknown entity, it just comes back with every
    value/status null/"NOT_FOUND", which this flattens to None for an
    easier check at the call site."""
    body = json.dumps(
        {
            "entities": {"entity_id": [entity_id]},
            "features": [f"{feature_view}:{name}" for name in features],
        }
    ).encode()
    req = urllib.request.Request(
        f"{FEAST_FEATURE_SERVER_URL}/get-online-features",
        data=body,
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    try:
        with urllib.request.urlopen(req, timeout=10) as resp:
            payload = json.loads(resp.read())
    except urllib.error.HTTPError as err:
        raise FeastClientError(f"feast rejected the request: {err.read().decode(errors='replace')}") from err
    except urllib.error.URLError as err:
        raise FeastClientError(f"couldn't reach the Feast feature server: {err}") from err

    # metadata.feature_names and results are positionally aligned (not
    # name-matched — verified against a real feature server: the
    # response's own feature order doesn't necessarily match the
    # request's), and always lead with the entity key itself (entity_id).
    names = payload["metadata"]["feature_names"]
    values = {name: col["values"][0] for name, col in zip(names, payload["results"])}
    if all(v is None for name, v in values.items() if name != "entity_id"):
        return None
    return {name: values.get(name) for name in features}
