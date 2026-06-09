import frappe
from frappe.utils import now_datetime, getdate


DEFAULT_WAREHOUSE = "Finished Goods - TM"


def on_submit(doc, method):
    """
    Triggered when Purchase Invoice is submitted.
    Creates one Pharmacy Batch per pharmacy line item.
    Updates current_stock on each Pharmacy Item.
    Syncs Item Price (Standard Selling) with batch expiry.
    Skips non-pharmacy lines (services, office supplies, etc.) silently.
    Skips entirely if doc.is_return (Purchase Return / Debit Note).
    """
    # Purchase Returns (Debit Notes) must NOT add stock
    if doc.is_return:
        return

    batch_ids_in_invoice = set()
    batches_created = 0

    for row in doc.items:
        # Lookup Pharmacy Item by item_code
        # (Pharmacy Item.item_code is the same SKU as Item.item_code)
        pharmacy_item_name = frappe.db.get_value(
            "Pharmacy Item",
            {"item_code": row.item_code},
            "name"
        )

        # Non-pharmacy line — skip silently
        if not pharmacy_item_name:
            continue

        # Validate row before any DB write (throws on first failure)
        _validate_pharmacy_row(row, doc, batch_ids_in_invoice)
        batch_ids_in_invoice.add(row.custom_batch_id)

        # Fetch Pharmacy Item master
        pi = frappe.get_doc("Pharmacy Item", pharmacy_item_name)

        # Derive computed values
        gst_pct = (row.igst_rate or 0) or (
            (row.cgst_rate or 0) + (row.sgst_rate or 0)
        )
        cost_price    = row.rate or 0
        selling_price = row.custom_selling_price or 0
        profit        = selling_price - cost_price
        warehouse     = (
            row.warehouse
            or doc.set_warehouse
            or DEFAULT_WAREHOUSE
        )

        # Create Pharmacy Batch
        batch = frappe.get_doc({
            "doctype":               "Pharmacy Batch",
            "item":                  pharmacy_item_name,
            "item_name":             pi.item_name,
            "batch_id":              row.custom_batch_id,
            "manufacturing_date":    row.custom_manufacturing_date,
            "expiry_date":           row.custom_expiry_date,
            "mrp_price":             row.custom_mrp_price or 0,
            "selling_price":         selling_price,
            "cost_price":            cost_price,
            "profit_per_uom":        profit,
            "gst_pct":               gst_pct,
            "purchase_tax_pct":      gst_pct,
            "supplier_discount_pct": row.discount_percentage or 0,
            "hsn_sac_code":          row.gst_hsn_code or pi.hsn_sac_code or "",
            "manufacturer_name":     pi.manufacturer_name or "",
            "supplier_name":         doc.supplier,
            "supplier_invoice_no":   doc.bill_no,
            "qty_received":          row.qty,
            "remaining_qty":         row.qty,
            "warehouse":             warehouse,
            "purchase_invoice":      doc.name,
            "stock_receipt":         None,
            "status":                "Active",
            "creation_date":         now_datetime()
        })
        batch.insert(ignore_permissions=True)
        frappe.db.commit()

        # Update current_stock on Pharmacy Item
        item_doc = frappe.get_doc("Pharmacy Item", pharmacy_item_name)
        item_doc.current_stock = (
            item_doc.current_stock or 0
        ) + (row.qty or 0)
        item_doc.save(ignore_permissions=True)

        # Sync Item Price (Standard Selling) — mirrors stock_receipt pattern
        if selling_price and selling_price > 0:
            _sync_item_price(
                item_code=row.item_code,
                selling_price=selling_price,
                batch_name=batch.name,
                expiry_date=row.custom_expiry_date
            )

        batches_created += 1

    frappe.db.commit()

    if batches_created:
        frappe.msgprint(
            f"Purchase Invoice submitted. "
            f"{batches_created} Pharmacy Batch(es) created and stock updated.",
            alert=True
        )


def on_cancel(doc, method):
    """
    Triggered when Purchase Invoice is cancelled.
    Quarantines all Pharmacy Batches created by this PI.
    Reverses current_stock by remaining_qty of each batch (floored at 0).
    Already-consumed quantities stay intact (sales are real, not reversed).
    Mirrors stock_receipt.on_cancel behaviour exactly.
    """
    if doc.is_return:
        return

    # Fetch all batches created by this Purchase Invoice
    # (one query for all line items — purchase_invoice field is the link)
    batches = frappe.get_all(
        "Pharmacy Batch",
        filters={"purchase_invoice": doc.name},
        fields=["name", "item", "remaining_qty"]
    )

    for b in batches:
        # Quarantine the batch — no more sales from it
        frappe.db.set_value(
            "Pharmacy Batch", b.name,
            "status", "Quarantined"
        )

        # Reduce current_stock by remaining (un-sold) qty, floor at 0
        item_doc = frappe.get_doc("Pharmacy Item", b.item)
        item_doc.current_stock = max(
            0,
            (item_doc.current_stock or 0) - (b.remaining_qty or 0)
        )
        item_doc.save(ignore_permissions=True)

    frappe.db.commit()


def _validate_pharmacy_row(row, doc, batch_ids_in_invoice):
    """
    Validate one pharmacy row before creating its Pharmacy Batch.
    Throws frappe.ValidationError on the first failure — rolls back the submit.
    All messages are prefixed with the row index so the user can spot
    the offending line instantly.
    """
    idx = row.idx

    # 1. Required field checks (in case mandatory_depends_on misfires)
    if not row.custom_batch_id:
        frappe.throw(
            f"Row #{idx}: Batch ID is required for pharmacy items."
        )
    if not row.custom_expiry_date:
        frappe.throw(
            f"Row #{idx}: Expiry Date is required for pharmacy items."
        )
    if not row.custom_mrp_price or row.custom_mrp_price <= 0:
        frappe.throw(
            f"Row #{idx}: MRP Price is required and must be greater than 0."
        )
    if not row.custom_selling_price or row.custom_selling_price <= 0:
        frappe.throw(
            f"Row #{idx}: Selling Price is required and must be greater than 0."
        )

    # 2. Cost price (Rate) must be > 0
    if not row.rate or row.rate <= 0:
        frappe.throw(
            f"Row #{idx}: Rate (cost price) must be greater than 0."
        )

    # 3. Expiry must be after posting date
    posting = getdate(doc.posting_date)
    expiry  = getdate(row.custom_expiry_date)
    if expiry <= posting:
        frappe.throw(
            f"Row #{idx}: Expiry Date ({expiry}) must be after "
            f"Posting Date ({posting})."
        )

    # 4. Manufacturing Date (if given) must be before Expiry Date
    if row.custom_manufacturing_date:
        mfg = getdate(row.custom_manufacturing_date)
        if mfg >= expiry:
            frappe.throw(
                f"Row #{idx}: Manufacturing Date ({mfg}) must be before "
                f"Expiry Date ({expiry})."
            )

    # 5. Batch ID must be unique WITHIN this invoice
    if row.custom_batch_id in batch_ids_in_invoice:
        frappe.throw(
            f"Row #{idx}: Batch ID '{row.custom_batch_id}' is duplicated "
            f"in this invoice."
        )

    # 6. Batch ID must be unique ACROSS all existing Pharmacy Batches
    existing = frappe.db.get_value(
        "Pharmacy Batch",
        {"batch_id": row.custom_batch_id},
        "name"
    )
    if existing:
        frappe.throw(
            f"Row #{idx}: Batch ID '{row.custom_batch_id}' already exists "
            f"(Pharmacy Batch: {existing}). Use a unique Batch ID."
        )


def _sync_item_price(item_code, selling_price,
                     batch_name, expiry_date):
    """
    Create or update Item Price in Standard Selling price list.
    VERBATIM copy of stock_receipt._sync_item_price for consistency.

    Does NOT use batch_no field (that links to native Batch doctype
    which is different from our Pharmacy Batch — causes errors).
    Uses valid_upto to store expiry date for visibility.
    FEFO rate selection is handled by get_fifo_rate() in
    sales_invoice.py — not by Item Price batch_no.
    """
    try:
        # Check if Item Price already exists for this item
        existing = frappe.get_all(
            "Item Price",
            filters={
                "item_code":  item_code,
                "price_list": "Standard Selling",
                "selling":    1,
                "batch_no":   ["in", ["", None]]
            },
            fields=["name"],
            limit_page_length=1
        )

        if existing:
            # Update existing Item Price with
            # latest selling price and expiry date
            frappe.db.set_value(
                "Item Price",
                existing[0].name, {
                    "price_list_rate": selling_price,
                    "valid_upto":      expiry_date
                }
            )
        else:
            # Create new Item Price
            ip = frappe.get_doc({
                "doctype":         "Item Price",
                "item_code":       item_code,
                "price_list":      "Standard Selling",
                "selling":         1,
                "price_list_rate": selling_price,
                "valid_upto":      expiry_date,
                "currency":        "INR"
            })
            ip.flags.ignore_permissions = True
            ip.flags.ignore_mandatory   = True
            ip.insert()

        frappe.db.commit()

        frappe.logger().info(
            f"Item Price synced: {item_code} "
            f"rate {selling_price} expiry {expiry_date}"
        )

    except Exception as e:
        frappe.log_error(
            f"Item Price sync failed for {item_code}: {str(e)}",
            "Item Price Sync Error"
        )
        frappe.msgprint(
            f"Item Price sync failed for {item_code}: {str(e)}",
            title="Item Price Sync Error",
            indicator="red"
        )