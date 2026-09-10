package trino

# Trino's native access control. Three things happen here, all applied by
# Trino itself during query planning:
#
#   1. allow      — table-level gate for SelectFromColumns, backed by the
#                    same policy data and the same authz.allow() logic the
#                    backend uses (see app.rego). This is what closes the
#                    "connect to Trino directly and skip the backend"
#                    gap: whoever's asking, Trino now asks the same
#                    question the backend would have.
#   2. rowFilters  — sales.orders restricted to the caller's own region,
#                    regardless of any table-level grant.
#   3. columnMask  — hr.salaries.salary hidden unless the caller is HR.
#
# Everything other than SelectFromColumns (browsing catalogs/schemas,
# connecting, etc.) stays open by default so Trino remains usable — this
# policy only gates the operation that actually reads data. One
# consequence: `SHOW TABLES` still lists table names a caller can't
# SELECT from; closing that too would mean wiring FilterTables/batch mode
# as well, not done here.

import data.authz
import future.keywords.if

default allow := true

current_user := input.context.identity.user

table_resource := input.action.resource.table

resource_key := sprintf("%s.%s", [table_resource.schemaName, table_resource.tableName])

is_select if input.action.operation == "SelectFromColumns"

# Fail CLOSED: if authz.allow doesn't positively return true — including
# when it's undefined because policy_data hasn't been pushed yet — this
# denies. `not <undefined>` evaluates to true in Rego, so an OPA/backend
# outage denies table reads instead of silently allowing them.
allow := false if {
	is_select
	not authz.allow(current_user, resource_key, "select")
}

# ---------------------------------------------------------------------------
# Row filter: sales.orders is restricted to the caller's own region,
# independent of whatever table-level grant they have.
# ---------------------------------------------------------------------------

is_sales_orders if {
	table_resource.catalogName == "postgres"
	table_resource.schemaName == "sales"
	table_resource.tableName == "orders"
}

rowFilters contains {"expression": sprintf("region = '%s'", [authz.attributes[current_user].region])} if {
	is_sales_orders
	authz.attributes[current_user]
}

# Unknown identity querying sales.orders: fail closed, show nothing.
rowFilters contains {"expression": "1 = 0"} if {
	is_sales_orders
	not authz.attributes[current_user]
}

# ---------------------------------------------------------------------------
# Column mask: hr.salaries.salary is NULL for everyone except HR.
# ---------------------------------------------------------------------------

column_resource := input.action.resource.column

is_hr_salary_column if {
	column_resource.catalogName == "postgres"
	column_resource.schemaName == "hr"
	column_resource.tableName == "salaries"
	column_resource.columnName == "salary"
}

is_hr if authz.attributes[current_user].hr == true

columnMask := {"expression": "NULL"} if {
	is_hr_salary_column
	not is_hr
}
