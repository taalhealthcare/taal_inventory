import base64
import hashlib
import hmac
import json

import frappe

SALES_ORDER_FIELD = "custom_order_category"

ORDER_CATEGORY_MAP = {
    "art":           "ART",
    "pep":           "PEP",
    "prep":          "PrEP",
    "sti":           "STI",
    "n/a":           "N/A",
    "na":            "N/A",
    "uncategorized": "UNCATEGORIZED",
    "uncategorised": "UNCATEGORIZED",
}


@frappe.whitelist(allow_guest=True)
def webhook():
    try:
        raw_body = frappe.request.get_data() or b""
        verify_webhook_signature(raw_body)
        payload   = parse_payload(raw_body)
        wc_order_id = str(payload.get("id") or "").strip()

        if not wc_order_id:
            return {"success": False, "reason": "Missing order ID"}

        category  = extract_category_from_payload(payload)
        wc_server = frappe.conf.get("taal_wc_server_name") or "taalhealthcare.com"

        if not category:
            return {
                "success":  True,
                "updated":  False,
                "reason":   "No supported order_category found",
                "woocommerce_id": wc_order_id,
            }

        so_name = find_sales_order(wc_order_id, wc_server)

        if not so_name:
            frappe.logger("taal_woocommerce").info(
                "Sales Order not found for WC Order %s — will be picked up by reconciler",
                wc_order_id,
            )
            return {
                "success": True,
                "updated": False,
                "pending": True,
                "reason":  "Sales Order not yet created",
                "woocommerce_id": wc_order_id,
                "category": category,
            }

        result = update_sales_order_category(so_name, category, wc_order_id)
        frappe.db.commit()

        return {
            "success":         True,
            "updated":         result["updated"],
            "woocommerce_id":  wc_order_id,
            "sales_order":     so_name,
            "category_before": result["before"],
            "category_after":  result["after"],
        }

    except frappe.AuthenticationError:
        raise
    except Exception:
        frappe.log_error(
            title="WooCommerce Category Webhook Failed",
            message=frappe.get_traceback(),
        )
        raise


def verify_webhook_signature(raw_body):
    secret = frappe.conf.get("taal_wc_webhook_secret")

    if not secret:
        frappe.log_error(
            title="WooCommerce Webhook Secret Missing",
            message="Add taal_wc_webhook_secret to site_config.json",
        )
        frappe.throw("Webhook not configured", frappe.AuthenticationError)

    received = (
        frappe.get_request_header("X-WC-Webhook-Signature") or ""
    ).strip()

    if not received:
        frappe.throw("Webhook signature missing", frappe.AuthenticationError)

    expected = base64.b64encode(
        hmac.new(
            secret.encode("utf-8"),
            raw_body,
            hashlib.sha256,
        ).digest()
    ).decode("utf-8")

    if not hmac.compare_digest(received, expected):
        frappe.throw("Invalid webhook signature", frappe.AuthenticationError)


def parse_payload(raw_body):
    try:
        payload = json.loads(raw_body.decode("utf-8"))
    except Exception:
        frappe.throw("Invalid JSON payload", frappe.ValidationError)

    if not isinstance(payload, dict):
        frappe.throw("Unexpected payload format", frappe.ValidationError)

    return payload


def extract_category_from_payload(payload):
    meta_data = payload.get("meta_data") or []

    if isinstance(meta_data, str):
        try:
            meta_data = json.loads(meta_data)
        except Exception:
            return None

    if not isinstance(meta_data, list):
        return None

    for item in meta_data:
        if not isinstance(item, dict):
            continue
        key = str(item.get("key") or "").strip().lower()
        if key != "order_category":
            continue
        raw = str(item.get("value") or "").strip().lower()
        return ORDER_CATEGORY_MAP.get(raw)

    return None


def find_sales_order(wc_order_id, wc_server=None):
    filters = {"woocommerce_id": str(wc_order_id)}

    if wc_server:
        filters["woocommerce_server"] = wc_server

    rows = frappe.get_all(
        "Sales Order",
        filters=filters,
        fields=["name"],
        order_by="creation desc",
        limit=1,
    )

    if not rows and wc_server:
        rows = frappe.get_all(
            "Sales Order",
            filters={"woocommerce_id": str(wc_order_id)},
            fields=["name"],
            order_by="creation desc",
            limit=1,
        )

    return rows[0].name if rows else None


def update_sales_order_category(so_name, category, wc_order_id=None):
    current = frappe.db.get_value("Sales Order", so_name, SALES_ORDER_FIELD)

    if current == category:
        return {"updated": False, "before": current, "after": current}

    frappe.db.set_value(
        "Sales Order", so_name, SALES_ORDER_FIELD,
        category, update_modified=False,
    )

    frappe.logger("taal_woocommerce").info(
        "WC#%s → SO %s category %s → %s",
        wc_order_id, so_name, current or "<blank>", category,
    )

    return {"updated": True, "before": current, "after": category}


def reconcile_recent_order_categories():
    """Safety-net: runs every 5 min via scheduler."""
    wc_orders = frappe.get_all(
        "WooCommerce Order",
        filters={
            "modified": [">=", frappe.utils.add_to_date(
                frappe.utils.now_datetime(), hours=-24
            )],
            "meta_data": ["is", "set"],
        },
        fields=["name", "id", "woocommerce_server", "meta_data"],
        order_by="modified desc",
        limit=500,
    )

    updated = 0
    failed  = 0

    for wco in wc_orders:
        try:
            category = extract_category_from_payload(
                {"meta_data": wco.meta_data}
            )
            if not category:
                continue

            so_name = find_sales_order(
                str(wco.id), wco.woocommerce_server
            )
            if not so_name:
                continue

            result = update_sales_order_category(
                so_name, category, str(wco.id)
            )
            if result["updated"]:
                updated += 1

        except Exception:
            failed += 1
            frappe.log_error(
                title="WC Category Reconciliation Failed: " + wco.name,
                message=frappe.get_traceback(),
            )

    frappe.db.commit()
    return {"processed": len(wc_orders), "updated": updated, "failed": failed}


@frappe.whitelist()
def sync_existing_order(woocommerce_order_name):
    """Manual sync — callable from bench execute."""
    wc_order  = frappe.get_doc("WooCommerce Order", woocommerce_order_name)
    category  = extract_category_from_payload({"meta_data": wc_order.meta_data})
    wc_server = frappe.conf.get("taal_wc_server_name") or "taalhealthcare.com"

    if not category:
        return {"success": False, "reason": "No supported order_category found"}

    so_name = find_sales_order(str(wc_order.id), wc_server)

    if not so_name:
        return {"success": False, "reason": "Linked Sales Order not found"}

    result = update_sales_order_category(so_name, category, str(wc_order.id))
    frappe.db.commit()

    return {
        "success":         True,
        "woocommerce_order": wc_order.name,
        "sales_order":     so_name,
        "category_before": result["before"],
        "category_after":  result["after"],
        "updated":         result["updated"],
    }