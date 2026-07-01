"""
taal_inventory/events/stock_receipt.py

Handles Stock Receipt submit, cancel, and Stock Adjustment submit/cancel.

UPDATED FOR MULTI-COMPANY SUPPORT:
  - Stock Receipt has a mandatory `company` field. Every new Pharmacy
    Batch created from a Stock Receipt carries this company.
  - Every deduction_log entry now also records `company` for full
    multi-company audit trail.
  - Calls recalculate_company_stock() to keep Pharmacy Item's
    company_stock child table in sync on every submit/cancel.

POINT 3 — STOCK ADJUSTMENT GL POSTING:
  - on_adjustment_submit now posts a Journal Entry recording the
    write-off loss (Debit Stock Adjustment, Credit Stock In Hand),
    grouped per company, valued at batch cost_price x qty.
  - "Opening Stock" adjustment type is GL-silent.
  - on_adjustment_cancel restores stock AND cancels the Journal Entry.
"""

import frappe
from frappe.utils import now_datetime, flt


# Adjustment types that should NOT post to GL
_GL_SILENT_ADJUSTMENT_TYPES = ["Opening Stock"]


def _get_pharmacy_item_name(row_item):
    """
    Resolves the correct Pharmacy Item document name from a Stock Receipt
    Item row's `item` field value.

    Stock Receipt stores either:
      - The Pharmacy Item document name  (e.g. TPCHC-PI-2026-0055) - new
      - The Pharmacy Item item_code      (e.g. TPCHC-PI-000054)    - old
    """
    if not row_item:
        return None

    direct = frappe.db.get_value("Pharmacy Item", row_item, "name")
    if direct:
        return direct

    by_code = frappe.db.get_value(
        "Pharmacy Item",
        {"item_code": row_item},
        "name"
    )
    return by_code


def on_submit(doc, method):
    """
    Triggered when Stock Receipt is submitted.
    Creates one Pharmacy Batch per line item, tagged with doc.company.
    Updates current_stock on each Pharmacy Item (global + per-company).
    Syncs Item Price (Standard Selling).
    Calculates Stock Receipt totals.
    Logs each batch creation as "Stock IN" in deduction_log, with company.
    """
    from taal_inventory.utils.stock_log import append_stock_log
    from taal_inventory.utils.company_stock import recalculate_company_stock

    if not doc.company:
        frappe.throw(
            "Company is mandatory on Stock Receipt. "
            "Please select which company this stock belongs to."
        )

    total_amount = 0
    total_tax    = 0

    for row in doc.items:
        pi_name = _get_pharmacy_item_name(row.item)
        if not pi_name:
            frappe.throw(
                f"Row #{row.idx}: Pharmacy Item '{row.item}' not found. "
                f"Please select a valid Pharmacy Item."
            )

        pi = frappe.get_doc("Pharmacy Item", pi_name)

        profit     = flt(row.selling_price or 0) - flt(row.cost_price or 0)
        line_total = flt(row.qty_received or 0) * flt(row.cost_price or 0)
        gst_pct    = flt(row.gst_pct or 0)
        pur_tax    = flt(row.purchase_tax_pct or 0)
        tax_amount = line_total * ((gst_pct + pur_tax) / 100)

        total_amount += line_total
        total_tax    += tax_amount

        # Create Pharmacy Batch - tagged with company
        batch = frappe.get_doc({
            "doctype":               "Pharmacy Batch",
            "item":                  pi_name,
            "item_name":             pi.item_name,
            "batch_id":              row.batch_id,
            "manufacturing_date":    row.manufacturing_date,
            "expiry_date":           row.expiry_date,
            "mrp_price":             flt(row.mrp_price or 0),
            "selling_price":         flt(row.selling_price or 0),
            "cost_price":            flt(row.cost_price or 0),
            "profit_per_uom":        profit,
            "gst_pct":               gst_pct,
            "purchsase_tax_pct":     pur_tax,
            "supplier_discount_pct": flt(row.discount_pct or 0),
            "hsn_sac_code":          pi.hsn_sac_code or "",
            "manufacturer_name":     pi.manufacturer_name or "",
            "supplier_name":         doc.supplier,
            "supplier_invoice_no":   doc.supplier_invoice_no,
            "qty_received":          flt(row.qty_received),
            "remaining_qty":         flt(row.qty_received),
            "warehouse":             doc.warehouse,
            "company":               doc.company,
            "stock_receipt":         doc.name,
            "status":                "Active",
            "creation_date":         now_datetime()
        })
        batch.insert(ignore_permissions=True)
        frappe.db.commit()

        # Update line_total on the SR row
        frappe.db.set_value(
            "Stock Receipt Item",
            row.name,
            "line_total",
            round(line_total, 2)
        )

        # Sync Item Price (Standard Selling)
        if row.selling_price and flt(row.selling_price) > 0:
            actual_item_code = frappe.db.get_value(
                "Pharmacy Item", pi_name, "item_code"
            )
            if actual_item_code:
                _sync_item_price(
                    item_code=actual_item_code,
                    selling_price=flt(row.selling_price),
                    batch_name=batch.name,
                    expiry_date=row.expiry_date
                )

        # Refresh company_stock child table AND global current_stock
        new_company_stock = recalculate_company_stock(pi_name, doc.company)

        # Log Stock IN - with company
        append_stock_log(
            pharmacy_item_name=pi_name,
            event_type="Stock IN",
            reference_doctype="Stock Receipt",
            reference_no=doc.name,
            batch_id=batch.name,
            qty=flt(row.qty_received),
            balance_after=new_company_stock,
            cost_price=flt(row.cost_price or 0),
            selling_price=flt(row.selling_price or 0),
            mrp_price=flt(row.mrp_price or 0),
            warehouse=doc.warehouse or "",
            company=doc.company,
            remarks=f"Batch {row.batch_id} created via SR {doc.name} "
                    f"for company {doc.company}"
        )

    grand_total = total_amount + total_tax
    frappe.db.set_value("Stock Receipt", doc.name, {
        "total_amount": round(total_amount, 2),
        "total_tax":    round(total_tax, 2),
        "grand_total":  round(grand_total, 2)
    })

    frappe.db.commit()
    frappe.msgprint(
        f"Stock Receipt submitted for {doc.company}. "
        f"Batches created and stock updated.",
        alert=True
    )


def on_cancel(doc, method):
    """
    Triggered when Stock Receipt is cancelled.
    Quarantines batches, reverses current_stock (global + per-company).
    Logs "Reversal OUT" per batch quarantined, with company.
    """
    from taal_inventory.utils.stock_log import append_stock_log
    from taal_inventory.utils.company_stock import recalculate_company_stock

    for row in doc.items:
        pi_name = _get_pharmacy_item_name(row.item)
        if not pi_name:
            frappe.log_error(
                f"SR Cancel: Pharmacy Item '{row.item}' not found "
                f"(SR: {doc.name}, row #{row.idx}). Skipped.",
                "SR Cancel - Item Not Found"
            )
            continue

        batches = frappe.get_all(
            "Pharmacy Batch",
            filters={
                "stock_receipt": doc.name,
                "item":          pi_name
            },
            fields=[
                "name", "remaining_qty", "company",
                "cost_price", "selling_price",
                "mrp_price", "warehouse"
            ]
        )

        for b in batches:
            remaining = flt(b.remaining_qty or 0)

            frappe.db.set_value(
                "Pharmacy Batch", b.name,
                "status", "Quarantined"
            )

            new_company_stock = recalculate_company_stock(pi_name, b.company)

            append_stock_log(
                pharmacy_item_name=pi_name,
                event_type="Reversal OUT",
                reference_doctype="Stock Receipt",
                reference_no=doc.name,
                batch_id=b.name,
                qty=remaining,
                balance_after=new_company_stock,
                cost_price=flt(b.cost_price or 0),
                selling_price=flt(b.selling_price or 0),
                mrp_price=flt(b.mrp_price or 0),
                warehouse=b.warehouse or "",
                company=b.company,
                remarks=f"Cancelled SR {doc.name} - batch quarantined"
            )

    frappe.db.set_value("Stock Receipt", doc.name, {
        "total_amount": 0,
        "total_tax":    0,
        "grand_total":  0
    })
    frappe.db.commit()


def on_adjustment_submit(doc, method):
    """
    Handles Stock Adjustment submit.
    Deducts stock per adjustment rows.
    Logs "Stock OUT" with full pricing AND company to deduction_log.
    Refreshes company_stock for the batch's company.

    POINT 3: posts a Journal Entry recording the write-off loss,
    grouped per company, valued at batch cost_price x qty. Skips GL
    for "Opening Stock" adjustment type.

    NOTE: Stock Adjustment Item.item is a direct Link -> Pharmacy Item.
    Stock Adjustment Item.batch_id is a direct Link -> Pharmacy Batch,
    which already carries its own `company` field - read from there.
    """
    from taal_inventory.utils.stock_log import append_stock_log
    from taal_inventory.utils.company_stock import recalculate_company_stock

    # Accumulate write-off value per company for the Journal Entry(ies)
    company_loss_totals = {}

    for row in doc.items:
        batch = frappe.get_doc("Pharmacy Batch", row.batch_id)

        balance_before = flt(batch.remaining_qty)
        new_qty        = max(0, balance_before - flt(row.qty))
        new_status     = "Exhausted" if new_qty == 0 else "Active"

        frappe.db.set_value("Pharmacy Batch", batch.name, {
            "remaining_qty": new_qty,
            "status":        new_status
        })

        frappe.db.set_value(
            "Stock Adjustment Item", row.name, {
                "balance_before": balance_before,
                "balance_after":  new_qty
            }
        )

        new_company_stock = recalculate_company_stock(row.item, batch.company)

        adj_type = doc.adjustment_type or ""
        reason   = doc.reason or ""
        remarks  = f"{adj_type} adjustment - {reason}" if reason else adj_type

        append_stock_log(
            pharmacy_item_name=row.item,
            event_type="Stock OUT",
            reference_doctype="Stock Adjustment",
            reference_no=doc.name,
            batch_id=row.batch_id,
            qty=flt(row.qty),
            balance_after=new_company_stock,
            cost_price=flt(batch.cost_price or 0),
            selling_price=flt(batch.selling_price or 0),
            mrp_price=flt(batch.mrp_price or 0),
            warehouse=doc.warehouse or batch.warehouse or "",
            company=batch.company,
            remarks=remarks
        )

        # Accumulate loss value for GL (skip if GL-silent type)
        if adj_type not in _GL_SILENT_ADJUSTMENT_TYPES:
            loss_value = flt(row.qty) * flt(batch.cost_price or 0)
            if loss_value > 0:
                company = batch.company
                if company not in company_loss_totals:
                    company_loss_totals[company] = 0
                company_loss_totals[company] += loss_value

    frappe.db.commit()

    # Post Journal Entry per company (POINT 3)
    for company, total_loss in company_loss_totals.items():
        if total_loss <= 0:
            continue
        _post_adjustment_journal_entry(doc, company, total_loss)

    frappe.db.commit()


def on_adjustment_cancel(doc, method):
    """
    POINT 3: Handles Stock Adjustment cancel.
    Restores stock to the batch (reverses the write-off), logs Reversal IN,
    refreshes company_stock, and cancels the linked Journal Entry.
    """
    from taal_inventory.utils.stock_log import append_stock_log
    from taal_inventory.utils.company_stock import recalculate_company_stock

    for row in doc.items:
        batch = frappe.get_doc("Pharmacy Batch", row.batch_id)

        # Restore the adjusted quantity back to the batch
        restored_qty = flt(batch.remaining_qty) + flt(row.qty)
        new_status   = "Active" if restored_qty > 0 else "Exhausted"

        frappe.db.set_value("Pharmacy Batch", batch.name, {
            "remaining_qty": restored_qty,
            "status":        new_status
        })

        new_company_stock = recalculate_company_stock(row.item, batch.company)

        append_stock_log(
            pharmacy_item_name=row.item,
            event_type="Reversal IN",
            reference_doctype="Stock Adjustment",
            reference_no=doc.name,
            batch_id=row.batch_id,
            qty=flt(row.qty),
            balance_after=new_company_stock,
            cost_price=flt(batch.cost_price or 0),
            selling_price=flt(batch.selling_price or 0),
            mrp_price=flt(batch.mrp_price or 0),
            warehouse=doc.warehouse or batch.warehouse or "",
            company=batch.company,
            remarks=f"Cancelled Stock Adjustment {doc.name} - stock restored"
        )

    # Cancel the linked Journal Entry(ies) (POINT 3)
    linked_jes = frappe.get_all(
        "Journal Entry",
        filters={
            "cheque_no": doc.name,          # SA name stamped here on creation
            "docstatus": 1
        },
        fields=["name"]
    )
    for je in linked_jes:
        je_doc = frappe.get_doc("Journal Entry", je.name)
        je_doc.cancel()

    frappe.db.commit()


def _post_adjustment_journal_entry(doc, company, total_loss):
    """
    Creates and submits a Journal Entry recording a stock write-off loss
    for one company.

        Debit   Stock Adjustment - {company}                (expense -> P&L)
        Credit  Stock Received But Not Billed - {company}     (stock value out)

    NOTE: We credit SRBNB (a Liability account), NOT Stock In Hand.
    Stock In Hand is account_type "Stock" which ERPNext protects from
    manual Journal Entries. SRBNB holds the stock's value in this setup.

    The Stock Adjustment name is stamped into cheque_no so the cancel
    handler can find and reverse this JE.
    """
    loss_account  = frappe.db.get_value("Company", company, "stock_adjustment_account")
    srbnb_account = frappe.db.get_value("Company", company, "stock_received_but_not_billed")
    cost_center   = frappe.db.get_value("Company", company, "cost_center")

    if not loss_account or not srbnb_account:
        frappe.log_error(
            f"Stock Adjustment {doc.name}: missing loss/SRBNB account for "
            f"company {company}. JE not posted.",
            "Stock Adjustment GL - Missing Account"
        )
        return

    je = frappe.get_doc({
        "doctype": "Journal Entry",
        "voucher_type": "Journal Entry",
        "company": company,
        "posting_date": doc.adjustment_date or frappe.utils.nowdate(),
        "cheque_no": doc.name,                       # link back to the SA
        "cheque_date": doc.adjustment_date or frappe.utils.nowdate(),
        "user_remark": (
            f"Stock write-off ({doc.adjustment_type}) via Stock Adjustment "
            f"{doc.name}. Reason: {doc.reason or 'N/A'}"
        ),
        "accounts": [
            {
                "account": loss_account,
                "debit_in_account_currency": total_loss,
                "cost_center": cost_center
            },
            {
                "account": srbnb_account,
                "credit_in_account_currency": total_loss,
                "cost_center": cost_center
            }
        ]
    })
    je.insert(ignore_permissions=True)
    je.submit()

    frappe.msgprint(
        f"Stock write-off of "
        f"{frappe.utils.fmt_money(total_loss, currency='INR')} "
        f"posted to accounts ({company}) - Journal Entry {je.name}.",
        alert=True
    )


def _sync_item_price(item_code, selling_price, batch_name, expiry_date):
    """Create or update Item Price in Standard Selling price list."""
    try:
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
            frappe.db.set_value(
                "Item Price",
                existing[0].name, {
                    "price_list_rate": selling_price,
                    "valid_upto":      expiry_date
                }
            )
        else:
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