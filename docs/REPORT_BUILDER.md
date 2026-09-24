# Custom report builder

`backend/app/reporting/builder.py`, `services/saved_report_service.py`, model `SavedReport` (table `saved_reports`). Screens: Analytics >
Report builder and Saved reports.

## It is an allowlist, not a query language

**Nothing a user sends is ever turned into SQL or evaluated.** A definition names a dataset and fields from a registry; everything else
is refused before any data is read. Rows come from the same reporting providers the standard screens use (so the numbers agree), are
limited to 20,000, and grouping and aggregating happen in Python with exact `Decimal` arithmetic. There are no free joins: related
names (customer, supplier) are already resolved by the dataset.

Datasets: **Sales** (detailed and quick, one row per bill), **Purchases**, **Inventory** (current position), **Customers**, **Suppliers**,
**Finance** (per day, week or month), **Expenses**, **Promotions**, **CRM segments**, **Loyalty**, and **Online Orders** (listed but
*Not Available*: not connected, so it cannot be run).

A definition (`POST /analytics/builder/preview`, `POST /analytics/reports`):

```json
{"dataset": "sales",
 "definition": {"columns": [], "group_by": ["kind"],
                "aggregations": [{"field": "total_amount", "op": "SUM"}],
                "filters": [{"field": "total_amount", "op": "gt", "value": "100"}],
                "sort": [{"field": "sum_total_amount", "direction": "desc"}]}}
```

* Filter operators by field kind: text `eq ne contains in`; date `eq ne gt gte lt lte`; number `eq ne gt gte lt lte` (and `in` for integers).
* Aggregations: `SUM AVG MIN MAX` on numbers, `MIN MAX` on dates, `COUNT` on anything. A SUM/AVG/MIN/MAX over a group that contains an
  unknown value (a blank profit) is itself **Not Available**, never computed as if the blank were zero.
* Limits: 20 columns, 5 group-by fields, 10 aggregations, 10 filters, 50 values in an `in`. Either detail `columns` **or** `group_by` with
  `aggregations`. Extra keys, unknown fields/operators/aggregations and non-numeric values are refused.
* The reporting period comes from the usual period filters; the definition holds no dates.

## Permissions

`ANALYTICS_CUSTOM_REPORT` for every builder route, plus the permissions of the dataset (Sales `REPORT_VIEW`, Purchases `PURCHASE_VIEW`,
Inventory `INVENTORY_VIEW`, Customers `CUSTOMER_VIEW` + `CRM_ANALYTICS_VIEW`, Suppliers `SUPPLIER_VIEW` + `PURCHASE_VIEW`, Finance
`FINANCE_VIEW`, Expenses `FINANCE_EXPENSE_VIEW`, CRM `CRM_ANALYTICS_VIEW`, Loyalty `LOYALTY_VIEW`). A field that exposes customer or supplier
names also needs `CUSTOMER_VIEW` / `SUPPLIER_VIEW`; without it the field is not offered and is refused as unknown. The catalog
(`GET /analytics/builder/datasets`) lists only what the caller may use, with the reason for the rest.

The definition is validated **when it is saved and again every time it runs, with the permissions of whoever runs it**: a report saved by an
owner cannot be run by a role that may not read its dataset, and a definition edited directly in the database still cannot reach anything
outside the allowlist (tested).

## Saved reports

Shop-scoped, unique by name, editable (`PUT`), and **archived, never deleted** (`POST .../archive`, `.../restore`; there is no HTTP `DELETE`).
Create, update, archive and restore are written to the audit log. A saved report can be exported (`saved-<id>`) and scheduled.
