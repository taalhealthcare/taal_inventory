"""
taal_inventory/events/pharmacy_stock_transfer.py

Handles Inter-Company Pharmacy Stock Transfer.

BUSINESS LOGIC:
  - Stock moves from `from_company` to `to_company`.
  - Partial transfers allowed — source batch remaining_qty reduces
    by the transferred qty only; remainder stays at source company.
  - A BRAND NEW Pharmacy Batch is created at the destination company
    with a new system-generated batch_id.
  - transfer_price (set by the source company) becomes the
    destination company's NEW cost_price.
  - Destination warehouse is always the fixed "Finished Goods - <abbr>"
    warehouse for the receiving company.
  - Full deduction_log integration, including `company`:
        Source company      -> event_type = "Transfer OUT", company=from_company
        Destination company -> event_type = "Transfer IN",  company=to_company

  - Pharmacy Item stays a SINGLE global record per item_code.
    Per-company stock breakdown lives in the `company_stock` child
    table, rebuilt from source by utils/company_stock.rebuild_item_stock.

STOCK REFRESH:
  Batch mutations here use frappe.db.set_value / direct insert, so the
  Pharmacy Batch hooks do not fire. Every affected Pharmacy Item is
  therefore rebuilt explicitly once, after the item loop finishes.
  No intermediate frappe.db.commit() calls — the whole submit is one
  atomic transaction, and uncommitted writes are already visible to the
  SQL reads inside rebuild_item_stock on the same connection.
"""

import frappe
from frappe.utils import now_datetime, flt, getdate


def on_submit(doc, method):
    """
    Triggered when Pharmacy Stock Transfer is submitted.

    For each row in doc.items:
      1. Validate source batch belongs to from_company and has enough qty
      2. Reduce source batch remaining_qty by transferred qty
      3. Mark source batch Exhausted if remaining_qty becomes 0
      4. Create a NEW Pharmacy Batch at to_company
      5. Log "Transfer OUT" at source and "Transfer IN" at destination
      6. Write new_batch + pricing reference fields back onto the row

    After the loop: rebuild current_stock + company_stock for every
    Pharmacy Item involved.
    """
    from taal_inventory.utils.stock_log import append_stock_log
    from taal_inventory.utils.company_stock import rebuild_item_stock, get_company_stock

    if doc.from_company == doc.to_company:
        frappe.throw(
            "From Company and To Company must be different. "
            "Cannot transfer stock within the same company."
        )

    to_company_abbr = frappe.db.get_value("Company", doc.to_company, "abbr")
    if not to_company_abbr:
        frappe.throw(f"Could not resolve abbreviation for company {doc.to_company}.")

    destination_warehouse = f"Finished Goods - {to_company_abbr}"
    if not frappe.db.exists("Warehouse", destination_warehouse):
        frappe.throw(
            f"Destination warehouse '{destination_warehouse}' does not exist. "
            f"Please create it for company {doc.to_company} before transferring stock."
        )

    total_transferred = 0
    affected_items    = set()

    for row in doc.items:
        # ── Step 1: Validate source batch ─────────────────────────────────────
        source_batch = frappe.get_doc("Pharmacy Batch", row.source_batch)

        if source_batch.company != doc.from_company:
            frappe.throw(
                f"Row #{row.idx}: Batch {row.source_batch} does not belong to "
                f"{doc.from_company}. It belongs to {source_batch.company}."
            )

        if source_batch.status != "Active":
            frappe.throw(
                f"Row #{row.idx}: Batch {row.source_batch} is not Active "
                f"(current status: {source_batch.status}). Only Active batches "
                f"can be transferred."
            )

        available    = flt(source_batch.remaining_qty)
        transfer_qty = flt(row.qty)

        if transfer_qty <= 0:
            frappe.throw(f"Row #{row.idx}: Transfer quantity must be greater than 0.")

        if transfer_qty > available:
            frappe.throw(
                f"Row #{row.idx}: Cannot transfer {transfer_qty} units. "
                f"Only {available} units available in batch {row.source_batch}."
            )

        # ── Step 2 & 3: Reduce source batch, update status ────────────────────
        new_source_remaining = available - transfer_qty
        new_source_status    = "Exhausted" if new_source_remaining == 0 else "Active"

        frappe.db.set_value("Pharmacy Batch", source_batch.name, {
            "remaining_qty": new_source_remaining,
            "status":        new_source_status
        })

        # ── Step 4: Create NEW Pharmacy Batch at destination company ──────────
        transfer_price   = flt(row.transfer_price)
        to_selling_price = flt(row.to_selling_price)
        to_mrp_price     = flt(row.to_mrp_price)
        profit           = to_selling_price - transfer_price

        new_batch = frappe.get_doc({
            "doctype":                  "Pharmacy Batch",
            "item":                     source_batch.item,
            "item_name":                source_batch.item_name,
            "batch_id":                 _generate_transfer_batch_id(
                                            source_batch.batch_id, doc.name),
            "manufacturing_date":       source_batch.manufacturing_date,
            "expiry_date":              source_batch.expiry_date,
            "mrp_price":                to_mrp_price,
            "selling_price":            to_selling_price,
            "cost_price":               transfer_price,
            "profit_per_uom":           profit,
            "gst_pct":                  source_batch.gst_pct,
            "purchsase_tax_pct":        source_batch.purchsase_tax_pct,
            "supplier_discount_pct":    0,
            "hsn_sac_code":             source_batch.hsn_sac_code,
            "manufacturer_name":        source_batch.manufacturer_name,
            "supplier_name":            doc.from_company,
            "supplier_invoice_no":      doc.name,
            "qty_received":             transfer_qty,
            "remaining_qty":            transfer_qty,
            "warehouse":                destination_warehouse,
            "company":                  doc.to_company,
            "transferred_from_company": doc.from_company,
            "transfer_reference":       doc.name,
            "status":                   "Active",
            "creation_date":            now_datetime()
        })
        new_batch.insert(ignore_permissions=True)

        # Write batch references and pricing snapshot back onto the row
        frappe.db.set_value(
            "Pharmacy Stock Transfer Item", row.name,
            {
                "new_batch":          new_batch.name,
                "available_qty":      available,
                "from_cost_price":    source_batch.cost_price,
                "from_selling_price": source_batch.selling_price,
                "from_mrp_price":     source_batch.mrp_price
            }
        )

        pharmacy_item_name = source_batch.item
        affected_items.add(pharmacy_item_name)

        # Live per-company balances for the log lines
        source_company_stock = get_company_stock(pharmacy_item_name, doc.from_company)
        dest_company_stock   = get_company_stock(pharmacy_item_name, doc.to_company)

        # ── Step 5: Log Transfer OUT at source ─────────────────────────────────
        append_stock_log(
            pharmacy_item_name=pharmacy_item_name,
            event_type="Transfer OUT",
            reference_doctype="Pharmacy Stock Transfer",
            reference_no=doc.name,
            batch_id=source_batch.name,
            qty=transfer_qty,
            balance_after=source_company_stock,
            cost_price=flt(source_batch.cost_price),
            selling_price=flt(source_batch.selling_price),
            mrp_price=flt(source_batch.mrp_price),
            warehouse=source_batch.warehouse or "",
            company=doc.from_company,
            remarks=f"Transferred to {doc.to_company} via {doc.name} "
                    f"(company stock at source after: {source_company_stock})"
        )

        # ── Step 6: Log Transfer IN at destination ─────────────────────────────
        append_stock_log(
            pharmacy_item_name=pharmacy_item_name,
            event_type="Transfer IN",
            reference_doctype="Pharmacy Stock Transfer",
            reference_no=doc.name,
            batch_id=new_batch.name,
            qty=transfer_qty,
            balance_after=dest_company_stock,
            cost_price=transfer_price,
            selling_price=to_selling_price,
            mrp_price=to_mrp_price,
            warehouse=destination_warehouse,
            company=doc.to_company,
            remarks=f"Received from {doc.from_company} via {doc.name}, "
                    f"new batch {new_batch.batch_id} created "
                    f"(company stock at destination after: {dest_company_stock})"
        )

        total_transferred += transfer_qty

    frappe.db.set_value("Pharmacy Stock Transfer", doc.name,
                        "total_qty", total_transferred)

    # ── Rebuild stock once per affected item ──────────────────────────────────
    for pharmacy_item_name in affected_items:
        rebuild_item_stock(pharmacy_item_name)

    frappe.msgprint(
        f"Stock Transfer submitted. {len(doc.items)} batch(es) transferred "
        f"from {doc.from_company} to {doc.to_company}. "
        f"Total quantity: {total_transferred}.",
        alert=True
    )


def on_cancel(doc, method):
    """
    Triggered when Pharmacy Stock Transfer is cancelled.

    Reverses the transfer symmetrically:
      1. Restore remaining_qty on source batch
      2. Reactivate source batch if it was Exhausted
      3. Quarantine the NEW batch created at destination
      4. Log reversal entries on both sides, each with correct company
      5. Rebuild stock for every affected Pharmacy Item
    """
    from taal_inventory.utils.stock_log import append_stock_log
    from taal_inventory.utils.company_stock import rebuild_item_stock, get_company_stock

    affected_items = set()

    for row in doc.items:
        if not row.new_batch:
            continue

        source_batch = frappe.get_doc("Pharmacy Batch", row.source_batch)
        new_batch    = frappe.get_doc("Pharmacy Batch", row.new_batch)

        transfer_qty = flt(row.qty)

        # ── Step 1 & 2: Restore source batch ───────────────────────────────────
        restored_qty = flt(source_batch.remaining_qty) + transfer_qty
        frappe.db.set_value("Pharmacy Batch", source_batch.name, {
            "remaining_qty": restored_qty,
            "status":        "Active"
        })

        # ── Step 3: Quarantine destination batch ───────────────────────────────
        frappe.db.set_value("Pharmacy Batch", new_batch.name, "status", "Quarantined")

        pharmacy_item_name = source_batch.item
        affected_items.add(pharmacy_item_name)

        source_company_stock = get_company_stock(pharmacy_item_name, doc.from_company)
        dest_company_stock   = get_company_stock(pharmacy_item_name, doc.to_company)

        # ── Step 4: Log reversal at source (stock restored) ────────────────────
        append_stock_log(
            pharmacy_item_name=pharmacy_item_name,
            event_type="Transfer IN",
            reference_doctype="Pharmacy Stock Transfer",
            reference_no=doc.name,
            batch_id=source_batch.name,
            qty=transfer_qty,
            balance_after=source_company_stock,
            cost_price=flt(source_batch.cost_price),
            selling_price=flt(source_batch.selling_price),
            mrp_price=flt(source_batch.mrp_price),
            warehouse=source_batch.warehouse or "",
            company=doc.from_company,
            remarks=f"Cancelled Transfer {doc.name} — stock restored at source"
        )

        # ── Log reversal at destination (stock removed) ─────────────────────────
        append_stock_log(
            pharmacy_item_name=pharmacy_item_name,
            event_type="Transfer OUT",
            reference_doctype="Pharmacy Stock Transfer",
            reference_no=doc.name,
            batch_id=new_batch.name,
            qty=transfer_qty,
            balance_after=dest_company_stock,
            cost_price=flt(new_batch.cost_price),
            selling_price=flt(new_batch.selling_price),
            mrp_price=flt(new_batch.mrp_price),
            warehouse=new_batch.warehouse or "",
            company=doc.to_company,
            remarks=f"Cancelled Transfer {doc.name} — batch quarantined at destination"
        )

    # ── Step 5: Rebuild stock once per affected item ──────────────────────────
    for pharmacy_item_name in affected_items:
        rebuild_item_stock(pharmacy_item_name)


# ─────────────────────────────────────────────────────────────────────────────
# INTERNAL HELPERS
# ─────────────────────────────────────────────────────────────────────────────

def _generate_transfer_batch_id(source_batch_id, transfer_doc_name):
    """
    Generates a new, unique batch_id for the destination company's batch.
    Format: <original_batch_id>-T<transfer doc suffix>
    Keeps traceability back to the original batch while being unique.
    """
    suffix = transfer_doc_name.split("-")[-1] if "-" in transfer_doc_name else transfer_doc_name
    return f"{source_batch_id}-T{suffix}"