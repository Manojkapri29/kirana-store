"""Service layer: all business rules and database transactions live here.

Planned boundaries (added phase by phase):
    inventory_service  - the ONLY writer of the stock ledger
    khata_service      - the ONLY writer of the customer ledger
    purchase / detailed_sale / quick_sale / return services
    costing, export and document-numbering services

Routers call services; services never import from `app.api`.
Read-only reporting queries will live in a separate `reporting` package.
"""
