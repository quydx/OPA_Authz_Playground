"""One shared Entity — every feature view in this project is dynamic now,
created/updated on demand by poc/feast/registry_api.py (the
idma-feast-admin service) when a wizard user checks "save this
transformation as a Feature View" and names it, rather than defined
statically here. `feast apply` (run once by poc/feast/Dockerfile's
entrypoint, idempotent) still needs this file to register the project +
this one entity before any feature view exists, so the registry isn't
completely empty on first boot.

Named `row_id`/`entity_id` rather than e.g. `order_id`: feature views can
now come from any source table with an `id` column (sales.orders,
hr.employees, org2_sales.orders, ...), not just sales.orders, so the
shared entity can't be named after one specific table's primary key.
Every dynamically-created feature view's Postgres table uses this same
`entity_id` column regardless of which table its data came from — see
registry_api.py's own module docstring for the rest of that story.
"""
from feast import Entity
from feast.value_type import ValueType

row_id = Entity(name="row_id", join_keys=["entity_id"], value_type=ValueType.INT64)

__all__ = ["row_id"]
