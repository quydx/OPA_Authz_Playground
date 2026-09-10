-- A second organization's data, same shape as sales.* (see
-- 02-demo-data.sql) but a fully separate schema — org-002's own tenant,
-- used to demonstrate that organization is an isolation boundary
-- independent of table/schema grants, not just another resource in the
-- same tree. See opa/policies/authz.rego's same_org() check and
-- backend/app/seed.py's RESOURCE_ORG / DEMO_USERS org fields.

CREATE SCHEMA IF NOT EXISTS org2_sales;

CREATE TABLE org2_sales.customers (
    id      SERIAL PRIMARY KEY,
    name    TEXT NOT NULL,
    email   TEXT NOT NULL,
    region  TEXT NOT NULL
);

CREATE TABLE org2_sales.orders (
    id           SERIAL PRIMARY KEY,
    customer_id  INT REFERENCES org2_sales.customers(id),
    amount       NUMERIC(10, 2) NOT NULL,
    order_date   DATE NOT NULL,
    region       TEXT NOT NULL
);

INSERT INTO org2_sales.customers (name, email, region) VALUES
    ('Hiroshi Tanaka', 'h.tanaka@globex.example',  'APAC'),
    ('Ingrid Larsson',  'i.larsson@globex.example', 'EMEA'),
    ('Mateo Rossi',     'm.rossi@globex.example',   'EMEA');

INSERT INTO org2_sales.orders (customer_id, amount, order_date, region) VALUES
    (1, 89.00,  '2026-02-01', 'APAC'),
    (2, 259.00, '2026-03-14', 'EMEA'),
    (3, 39.00,  '2026-04-22', 'EMEA');
