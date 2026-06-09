app_name = "taal_inventory"
app_title = "Taal Inventory"
app_publisher = "TAAL+ Healthcare"
app_description = "Pharmacy Inventory Management for TAAL+ Healthcare"
app_version = "1.0.0"
app_license = "MIT"

# ── DocType JS Files ─────────────────────────────────────────────
doctype_js = {
    "Stock Receipt":  "public/js/stock_receipt.js",
    "Sales Invoice":  "public/js/sales_invoice.js",
}

# ── Scheduler Jobs ──────────────────────────────────────────────
scheduler_events = {
    "daily": [
        "taal_inventory.tasks.check_expiry_alerts",
        "taal_inventory.tasks.check_low_stock_daily",
    ],
}

# ── Document Event Hooks ─────────────────────────────────────────
doc_events = {
    "Sales Invoice": {
        "on_submit": "taal_inventory.events.sales_invoice.on_submit",
        "on_cancel": "taal_inventory.events.sales_invoice.on_cancel",
    },
    "Stock Receipt": {
        "on_submit": "taal_inventory.events.stock_receipt.on_submit",
        "on_cancel": "taal_inventory.events.stock_receipt.on_cancel",
    },
    "Stock Adjustment": {
        "on_submit": "taal_inventory.events.stock_receipt.on_adjustment_submit",
    },
    "Pharmacy Item": {
        "after_insert": "taal_inventory.events.pharmacy_item.after_insert",
        "on_update":    "taal_inventory.events.pharmacy_item.on_update",
    },
    "Purchase Invoice": {
        "on_submit": "taal_inventory.events.purchase_invoice.on_submit",
        "on_cancel": "taal_inventory.events.purchase_invoice.on_cancel",
    },
}