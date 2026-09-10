-- Sample business data, queried through Trino's postgresql connector.
-- Two schemas with different sensitivity, used to demonstrate resource-tree
-- authorization: a grant on "sales" or "hr" should reach every table below it.

CREATE SCHEMA IF NOT EXISTS sales;

CREATE TABLE sales.customers (
    id      SERIAL PRIMARY KEY,
    name    TEXT NOT NULL,
    email   TEXT NOT NULL,
    region  TEXT NOT NULL
);

CREATE TABLE sales.orders (
    id           SERIAL PRIMARY KEY,
    customer_id  INT REFERENCES sales.customers(id),
    amount       NUMERIC(10, 2) NOT NULL,
    order_date   DATE NOT NULL,
    -- Denormalized from customers.region so the OPA row-filter policy can
    -- express itself as a plain "region = '<caller's region>'" predicate.
    region       TEXT NOT NULL
);

CREATE TABLE sales.products (
    id     SERIAL PRIMARY KEY,
    name   TEXT NOT NULL,
    price  NUMERIC(10, 2) NOT NULL
);

INSERT INTO sales.customers (name, email, region) VALUES
    ('Nguyen Van A', 'a.nguyen@example.com', 'APAC'),
    ('Tran Thi B',   'b.tran@example.com',   'APAC'),
    ('Carlos Mendez','c.mendez@example.com', 'LATAM'),
    ('Priya Shah',   'p.shah@example.com',   'APAC'),
    ('Emma Wilson',  'e.wilson@example.com', 'EMEA');

INSERT INTO sales.products (name, price) VALUES
    ('Standard Plan', 49.00),
    ('Pro Plan',      149.00),
    ('Enterprise Add-on', 499.00);

-- customer 1, 2, 4 = APAC · customer 3 = LATAM · customer 5 = EMEA
INSERT INTO sales.orders (customer_id, amount, order_date, region) VALUES
    (1, 149.00, '2026-01-12', 'APAC'),
    (1, 499.00, '2026-03-02', 'APAC'),
    (2, 49.00,  '2026-02-20', 'APAC'),
    (3, 149.00, '2026-04-05', 'LATAM'),
    (4, 49.00,  '2026-05-18', 'APAC'),
    (5, 499.00, '2026-06-30', 'EMEA');

CREATE SCHEMA IF NOT EXISTS hr;

CREATE TABLE hr.employees (
    id           SERIAL PRIMARY KEY,
    name         TEXT NOT NULL,
    department   TEXT NOT NULL,
    hire_date    DATE NOT NULL
);

CREATE TABLE hr.salaries (
    employee_id  INT REFERENCES hr.employees(id),
    salary       NUMERIC(10, 2) NOT NULL,
    currency     TEXT NOT NULL
);

INSERT INTO hr.employees (name, department, hire_date) VALUES
    ('Le Thi C',    'Engineering', '2023-04-10'),
    ('David Kim',   'Engineering', '2022-09-01'),
    ('Fatima Noor', 'People Ops',  '2024-01-15');

INSERT INTO hr.salaries (employee_id, salary, currency) VALUES
    (1, 2400.00, 'USD'),
    (2, 3100.00, 'USD'),
    (3, 2100.00, 'USD');
