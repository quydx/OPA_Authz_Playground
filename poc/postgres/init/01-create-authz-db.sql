-- The default POSTGRES_DB ("demo") holds the business data Trino queries.
-- Policy data (grants, group membership, resource tree, user attributes)
-- lives in its own database on the same instance — the durable system of
-- record that the backend reads/writes and mirrors into OPA on every
-- change.
CREATE DATABASE authz;
