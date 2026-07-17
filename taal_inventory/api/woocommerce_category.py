import base64
import hashlib
import hmac
import json

import frappe

SALES_ORDER_FIELD = "custom_order_category"
LOG_TITLE = "TAAL WC Category"

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


def log(msg):
    frappe.log_error(title=LOG_TITLE, message=msg)


@frappe.whitelist(allow_guest=True)
def webhook():
    """
    Receive WooCommerce Order Updated webhook.
    Every step is logged to Error Log for diagnosis.
    """
    try:
        # ── Step 1: Confirm endpoint is being hit ─────────────────
        log("STEP 1: Webhook endpoint reached")

        raw_body = frappe.request.get_data() or b""
        log("STEP 2: Raw body length = " + str(len(raw_body)))

        if not raw_body:
            log("STEP 2 FAIL: Empty body received")
            return {"success": False, "reason": "Empty body"}

        # ── Step 3: Log all incoming headers ──────────────────────
        sig_header = (
            frappe.get_request_header("X-WC-Webhook-Signature") or ""
        ).strip()

        topic_header = (
            frappe.get_request_header("X-WC-Webhook-Topic") or ""
        ).strip()

        source_header = (
            frappe.get_request_header("X-WC-Webhook-Source") or ""
        ).strip()

        log(
            "STEP 3: Headers received\n"
            "  X-WC-Webhook-Signature : " + (sig_header[:20] + "..." if sig_header else "<MISSING>") + "\n"
            "  X-WC-Webhook-Topic     : " + (topic_header or "<MISSING>") + "\n"
            "  X-WC-Webhook-Source    : " + (source_header or "<MISSING>")
        )

        # ── Step 4: Parse JSON payload ─────────────────────────────
        try:
            payload = json.loads(raw_body.decode("utf-8"))
            log("STEP 4: JSON parsed successfully")
        except Exception as e:
            log("STEP 4 FAIL: JSON parse error — " + str(e))
            return {"success": False, "reason": "Invalid JSON"}

        wc_order_id = str(payload.get("id") or "").strip()
        log("STEP 5: WooCommerce Order ID = " + wc_order_id)

        # ── Step 6: Signature verification ────────────────────────
        secret = frappe.conf.get("taal_wc_webhook_secret")
        log("STEP 6: Secret configured = " + str(bool(secret)))

        if secret:
            if sig_header:
                expected = base64.b64encode(
                    hmac.new(
                        secret.encode("utf-8"),
                        raw_body,
                        hashlib.sha256,
                    ).digest()
                ).decode("utf-8")

                sig_match = hmac.compare_digest(sig_header, expected)
                log(
                    "STEP 6: Signature check\n"
                    "  Received : " + sig_header[:20] + "...\n"
                    "  Expected : " + expected[:20] + "...\n"
                    "  Match    : " + str(sig_match)
                )

                if not sig_match:
                    log("STEP 6 FAIL: Signature mismatch — request rejected")
                    frappe.throw(
                        "Invalid webhook signature",
                        frappe.AuthenticationError,
                    )
            else:
                log("STEP 6 WARN: No signature header — skipping verification for now")
        else:
            log("STEP 6 WARN: taal_wc_webhook_secret not set in site config — skipping verification")

        # ── Step 7: Extract order_category from meta_data ─────────
        meta_data = payload.get("meta_data") or []
        log("STEP 7: meta_data type = " + type(meta_data).__name__ + ", length = " + str(len(meta_data) if isinstance(meta_data, list) else "N/A"))

        category = extract_category_from_payload(payload)
        log("STEP 8: Extracted category = " + str(category))

        if not category:
            log("STEP 8: No valid order_category in meta_data — nothing to update")
            return {
                "success":        True,
                "updated":        False,
                "reason":         "No supported order_category found",
                "woocommerce_id": wc_order_id,
            }

        # ── Step 9: Find linked Sales Order ───────────────────────
        wc_server = (
            frappe.conf.get("taal_wc_server_name") or "taalhealthcare.com"
        )

        so_name = find_sales_order(wc_order_id, wc_server)
        log("STEP 9: Linked Sales Order = " + str(so_name))

        if not so_name:
            log("STEP 9: No linked Sales Order found for WC#" + wc_order_id)
            return {
                "success":        True,
                "updated":        False,
                "pending":        True,
                "reason":         "Sales Order not yet created",
                "woocommerce_id": wc_order_id,
                "category":       category,
            }

        # ── Step 10: Update the field ──────────────────────────────
        result = update_sales_order_category(so_name, category, wc_order_id)
        frappe.db.commit()

        log(
            "STEP 10: Update result\n"
            "  Sales Order     : " + so_name + "\n"
            "  Category Before : " + str(result["before"]) + "\n"
            "  Category After  : " + str(result["after"]) + "\n"
            "  Updated         : " + str(result["updated"])
        )

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
            title=LOG_TITLE + " — Unhandled Exception",
            message=frappe.get_traceback(),
        )
        raise


@frappe.whitelist(allow_guest=True)
def ping():
    """
    Simple health-check endpoint.
    Test: https://erp.taalhealthcare.com/api/method/taal_inventory.api.woocommerce_category.ping
    """
    log("PING: Endpoint reached — deployment confirmed")
    return {
        "status":  "ok",
        "message": "TAAL WC Category endpoint is active",
    }


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
    current = frappe.db.get_value(
        "Sales Order", so_name, SALES_ORDER_FIELD
    )

    if current == category:
        return {"updated": False, "before": current, "after": current}

    frappe.db.set_value(
        "Sales Order", so_name, SALES_ORDER_FIELD,
        category, update_modified=False,
    )

    return {"updated": True, "before": current, "after": category}


def reconcile_recent_order_categories():
    """Safety-net scheduler — runs every 5 minutes."""
    wc_orders = frappe.get_all(
        "WooCommerce Order",
        filters={
            "modified": [
                ">=",
                frappe.utils.add_to_date(
                    frappe.utils.now_datetime(), hours=-24
                ),
            ],
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
                title=LOG_TITLE + " Reconciler Failed: " + wco.name,
                message=frappe.get_traceback(),
            )

    frappe.db.commit()
    return {
        "processed": len(wc_orders),
        "updated":   updated,
        "failed":    failed,
    }


@frappe.whitelist()
def sync_existing_order(woocommerce_order_name):
    """Manual sync — for bench execute testing."""
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