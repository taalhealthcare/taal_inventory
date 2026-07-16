import json

import frappe


ORDER_CATEGORY_MAP = {
    "art": "ART",
    "pep": "PEP",
    "prep": "PrEP",
    "sti": "STI",
    "n/a": "N/A",
    "na": "N/A",
    "uncategorized": "UNCATEGORIZED",
    "uncategorised": "UNCATEGORIZED",
}

META_KEY = "order_category"
SALES_ORDER_FIELD = "custom_order_category"


def after_insert(doc, method=None):
    """
    Synchronize category when a WooCommerce Order is first created
    inside ERPNext.
    """
    sync_order_category(doc)


def on_update(doc, method=None):
    """
    Synchronize category whenever an existing WooCommerce Order is
    updated by webhook, scheduler, manual sync, or API request.
    """
    sync_order_category(doc)


def sync_order_category(woocommerce_order):
    """
    Copy order_category from WooCommerce Order.meta_data to the linked
    Sales Order.custom_order_category.
    """

    try:
        wc_order_id = str(
            woocommerce_order.get("id") or ""
        ).strip()

        wc_server = str(
            woocommerce_order.get("woocommerce_server") or ""
        ).strip()

        if not wc_order_id:
            frappe.logger("taal_woocommerce").warning(
                "Order category sync skipped: "
                "WooCommerce Order %s has no id",
                woocommerce_order.name,
            )
            return False

        category = extract_order_category(
            woocommerce_order.get("meta_data")
        )

        # Do not clear a Sales Order category if WooCommerce sends
        # incomplete metadata.
        if not category:
            frappe.logger("taal_woocommerce").info(
                "No valid order_category found for "
                "WooCommerce Order %s, id %s",
                woocommerce_order.name,
                wc_order_id,
            )
            return False

        sales_order_name = find_linked_sales_order(
            wc_order_id=wc_order_id,
            wc_server=wc_server,
        )

        if not sales_order_name:
            frappe.logger("taal_woocommerce").info(
                "Sales Order not available yet for "
                "WooCommerce Order id %s",
                wc_order_id,
            )
            return False

        current_category = frappe.db.get_value(
            "Sales Order",
            sales_order_name,
            SALES_ORDER_FIELD,
        )

        if current_category == category:
            return False

        frappe.db.set_value(
            "Sales Order",
            sales_order_name,
            SALES_ORDER_FIELD,
            category,
            update_modified=False,
        )

        frappe.logger("taal_woocommerce").info(
            "Updated Sales Order %s category from %s to %s "
            "using WooCommerce Order %s",
            sales_order_name,
            current_category or "<blank>",
            category,
            woocommerce_order.name,
        )

        return True

    except Exception:
        frappe.log_error(
            title=(
                "WooCommerce Order Category "
                "Synchronization Failed"
            ),
            message=frappe.get_traceback(),
        )
        raise


def sync_category_when_sales_order_created(
    doc,
    method=None,
):
    """
    Populate the category when the Sales Order is created after the
    WooCommerce Order document.

    This covers the timing case where WooCommerce Order.after_insert
    runs before WooCommerce Fusion creates the Sales Order.
    """

    try:
        wc_order_id = str(
            doc.get("woocommerce_id") or ""
        ).strip()

        wc_server = str(
            doc.get("woocommerce_server") or ""
        ).strip()

        if not wc_order_id:
            return

        # Preserve a category if it has already been populated.
        if doc.get(SALES_ORDER_FIELD):
            return

        filters = {
            "id": wc_order_id,
        }

        if wc_server:
            filters["woocommerce_server"] = wc_server

        wc_order_name = frappe.db.get_value(
            "WooCommerce Order",
            filters,
            "name",
        )

        # Controlled fallback for old records without server mapping.
        if not wc_order_name and wc_server:
            wc_order_name = frappe.db.get_value(
                "WooCommerce Order",
                {"id": wc_order_id},
                "name",
            )

        if not wc_order_name:
            frappe.logger("taal_woocommerce").info(
                "WooCommerce Order not found while creating "
                "Sales Order %s for WooCommerce id %s",
                doc.name,
                wc_order_id,
            )
            return

        meta_data = frappe.db.get_value(
            "WooCommerce Order",
            wc_order_name,
            "meta_data",
        )

        category = extract_order_category(meta_data)

        if not category:
            return

        frappe.db.set_value(
            "Sales Order",
            doc.name,
            SALES_ORDER_FIELD,
            category,
            update_modified=False,
        )

        frappe.logger("taal_woocommerce").info(
            "Populated category %s on newly created "
            "Sales Order %s",
            category,
            doc.name,
        )

    except Exception:
        frappe.log_error(
            title=(
                "Sales Order Initial Category "
                "Synchronization Failed"
            ),
            message=frappe.get_traceback(),
        )
        raise


def extract_order_category(meta_data):
    """
    Parse WooCommerce meta_data and return a valid ERPNext category.

    Supports:
    - JSON string
    - Python list
    """

    if not meta_data:
        return None

    if isinstance(meta_data, str):
        try:
            meta_items = json.loads(meta_data)
        except (TypeError, ValueError):
            frappe.log_error(
                title="Invalid WooCommerce Order Meta Data",
                message=(
                    "Could not parse meta_data as JSON.\n\n"
                    f"meta_data: {meta_data}"
                ),
            )
            return None

    elif isinstance(meta_data, list):
        meta_items = meta_data

    else:
        frappe.log_error(
            title="Unexpected WooCommerce Meta Data Type",
            message=(
                "Expected string or list, received "
                f"{type(meta_data).__name__}: {meta_data}"
            ),
        )
        return None

    for item in meta_items:
        if not isinstance(item, dict):
            continue

        key = str(
            item.get("key") or ""
        ).strip().lower()

        if key != META_KEY:
            continue

        raw_value = str(
            item.get("value") or ""
        ).strip()

        if not raw_value:
            return None

        category = ORDER_CATEGORY_MAP.get(
            raw_value.lower()
        )

        if category:
            return category

        frappe.log_error(
            title="Unsupported WooCommerce Order Category",
            message=(
                "Received unsupported order category: "
                f"{raw_value}\n"
                "Supported normalized values: "
                f"{', '.join(ORDER_CATEGORY_MAP.keys())}"
            ),
        )

        return None

    return None


def find_linked_sales_order(
    wc_order_id,
    wc_server=None,
):
    """
    Find the Sales Order using WooCommerce ID and server.

    The server condition prevents accidental matches if multiple
    WooCommerce stores use the same order number.
    """

    filters = {
        "woocommerce_id": str(wc_order_id),
    }

    if wc_server:
        filters["woocommerce_server"] = wc_server

    sales_orders = frappe.get_all(
        "Sales Order",
        filters=filters,
        fields=["name", "creation"],
        order_by="creation desc",
        limit=2,
    )

    if not sales_orders and wc_server:
        # Fallback for historical records where woocommerce_server
        # may be blank.
        sales_orders = frappe.get_all(
            "Sales Order",
            filters={
                "woocommerce_id": str(wc_order_id),
            },
            fields=["name", "creation"],
            order_by="creation desc",
            limit=2,
        )

    if not sales_orders:
        return None

    if len(sales_orders) > 1:
        frappe.log_error(
            title="Duplicate WooCommerce Sales Order Mapping",
            message=(
                "Multiple Sales Orders were found for "
                f"WooCommerce Order ID {wc_order_id}: "
                f"{', '.join(row.name for row in sales_orders)}"
            ),
        )

    return sales_orders[0].name


def backfill_all_order_categories():
    """
    One-time utility to populate Order Category on historical linked
    Sales Orders.

    This function is not a scheduler job. Run it manually once after
    deployment.
    """

    wc_orders = frappe.get_all(
        "WooCommerce Order",
        filters={
            "meta_data": ["is", "set"],
        },
        pluck="name",
    )

    processed = 0
    updated = 0
    failed = 0
    not_linked = 0
    no_category = 0

    for wc_order_name in wc_orders:
        processed += 1

        try:
            wc_order = frappe.get_doc(
                "WooCommerce Order",
                wc_order_name,
            )

            category = extract_order_category(
                wc_order.get("meta_data")
            )

            if not category:
                no_category += 1
                continue

            sales_order_name = find_linked_sales_order(
                wc_order_id=str(wc_order.id),
                wc_server=wc_order.woocommerce_server,
            )

            if not sales_order_name:
                not_linked += 1
                continue

            current_category = frappe.db.get_value(
                "Sales Order",
                sales_order_name,
                SALES_ORDER_FIELD,
            )

            if current_category == category:
                continue

            frappe.db.set_value(
                "Sales Order",
                sales_order_name,
                SALES_ORDER_FIELD,
                category,
                update_modified=False,
            )

            updated += 1

        except Exception:
            failed += 1

            frappe.log_error(
                title=(
                    "Order Category Backfill Failed: "
                    f"{wc_order_name}"
                ),
                message=frappe.get_traceback(),
            )

    frappe.db.commit()

    return {
        "processed": processed,
        "updated": updated,
        "already_correct": (
            processed
            - updated
            - failed
            - not_linked
            - no_category
        ),
        "no_category": no_category,
        "not_linked": not_linked,
        "failed": failed,
    }