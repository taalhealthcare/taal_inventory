"""
Interakt Outbound Service

Thin client for sending WhatsApp template messages via Interakt's public API.

API: POST {base_url}/v1/public/message/
Auth: HTTP Basic with the Secret API Key (Interakt provides it pre-base64-encoded,
      so we just pass it after "Basic " — no further encoding needed on our side)

Reads credentials from `Booking API Settings` (singleton DocType).
"""

import json
import requests
import frappe


HTTP_TIMEOUT = 15  # seconds


def send_template_message(phone_number, template_name, body_values):
    """
    Send a WhatsApp template message via Interakt.

    Args:
        phone_number (str): Recipient phone in any reasonable format.
                            Will be normalized to "+91" + 10-digit form.
                            Accepts "917679944473", "+917679944473",
                            "7679944473", "07679944473", etc.
        template_name (str): An approved Interakt template name.
        body_values (list[str]): Strings for the {{1}}, {{2}}, ... placeholders
                                 in the template's body, in order.

    Returns:
        dict: {
            "success": bool,
            "response": dict (when success=True, the raw Interakt response),
            "error": str (when success=False)
        }
    """
    try:
        settings = frappe.get_single("Booking API Settings")

        # --- API key ---
        api_key = settings.get_password("interakt_api_key") \
            if hasattr(settings, "get_password") else settings.interakt_api_key

        if not api_key:
            return {
                "success": False,
                "error": "Interakt API key is not configured in Booking API Settings"
            }

        base_url = (settings.interakt_api_base_url or "https://api.interakt.ai").rstrip("/")
        language = settings.template_language_code or "en"

        # --- Normalize phone to 12-digit "91XXXXXXXXXX" ---
        normalized = _normalize_phone(phone_number)
        if not _is_valid_indian_phone(normalized):
            return {
                "success": False,
                "error": f"Invalid phone number: {phone_number} (normalized to {normalized})"
            }

        country_code = "+91"
        local_number = normalized[2:]  # strip "91" prefix → 10 digits

        # --- Build payload ---
        payload = {
            "countryCode": country_code,
            "phoneNumber": local_number,
            "type": "Template",
            "template": {
                "name": template_name,
                "languageCode": language,
                "bodyValues": [str(v) if v is not None else "-" for v in body_values]
            }
        }

        url = f"{base_url}/v1/public/message/"
        response = requests.post(
            url,
            headers={
                "Authorization": f"Basic {api_key}",
                "Content-Type": "application/json"
            },
            data=json.dumps(payload),
            timeout=HTTP_TIMEOUT
        )

        # --- Check HTTP status ---
        if response.status_code not in (200, 201):
            return {
                "success": False,
                "error": f"Interakt HTTP {response.status_code}: {response.text[:400]}"
            }

        # --- Check response body for explicit failure ---
        try:
            resp_json = response.json()
        except Exception:
            return {
                "success": False,
                "error": f"Interakt returned non-JSON response: {response.text[:200]}"
            }

        # Interakt returns {"result": true, "message": "...", "id": "..."} on success
        if resp_json.get("result") is False:
            return {
                "success": False,
                "error": f"Interakt API rejected: {resp_json.get('message', 'unknown')}"
            }

        return {
            "success": True,
            "response": resp_json
        }

    except requests.Timeout:
        return {
            "success": False,
            "error": "Interakt API request timed out"
        }
    except Exception as e:
        frappe.log_error(frappe.get_traceback(), "Interakt Outbound Error")
        return {
            "success": False,
            "error": str(e)
        }


# ──────────────────────────────────────────────────────────────────────────────
# Phone helpers
# ──────────────────────────────────────────────────────────────────────────────

def _normalize_phone(phone):
    """
    Normalize to '91XXXXXXXXXX' (12 digits, no +).
    Drops +, spaces, dashes, parens. Adds 91 prefix if missing.
    """
    if not phone:
        return ""

    s = str(phone).strip()
    for ch in ("+", " ", "-", "(", ")"):
        s = s.replace(ch, "")

    if not s.isdigit():
        return ""

    if len(s) == 10:
        return "91" + s
    if len(s) == 11 and s.startswith("0"):
        return "91" + s[1:]
    if len(s) == 12 and s.startswith("91"):
        return s

    return s  # something unusual — return as-is and let validator reject it


def _is_valid_indian_phone(normalized):
    """Returns True if the normalized number looks like a valid +91 mobile."""
    if not normalized:
        return False
    if len(normalized) != 12:
        return False
    if not normalized.startswith("91"):
        return False
    if not normalized.isdigit():
        return False
    # Indian mobile numbers start with 6, 7, 8, or 9
    if normalized[2] not in ("6", "7", "8", "9"):
        return False
    return True