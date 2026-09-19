#!/usr/bin/env python3
"""Validate the Cute Things demo boundary without making network calls.

This is intentionally a small, dependency-free gate. It prevents a demo run
from inheriting the Buttons Bebe shop, production Gorgias, or the production
queue database through a typo or an unset environment override.
"""

from __future__ import annotations

import argparse
import pathlib
import sys


TARGET_SHOP = "yznyc1-ez.myshopify.com"
CLIENT_SHOP = "buttons-bebe.myshopify.com"
CLIENT_GORGIAS = "buttonsbebe"


def load_env(path: pathlib.Path) -> dict[str, str]:
    values: dict[str, str] = {}
    for line_number, raw in enumerate(path.read_text(encoding="utf-8").splitlines(), 1):
        line = raw.strip()
        if not line or line.startswith("#"):
            continue
        if "=" not in line:
            raise ValueError(f"{path}:{line_number}: expected KEY=VALUE")
        key, value = line.split("=", 1)
        values[key.strip()] = value.strip().strip('"').strip("'")
    return values


def validate(values: dict[str, str]) -> list[str]:
    errors: list[str] = []

    if values.get("DEMO_MODE") != "1":
        errors.append("DEMO_MODE must be 1")
    if values.get("DEMO_SHOP_DOMAIN") != TARGET_SHOP:
        errors.append(f"DEMO_SHOP_DOMAIN must be {TARGET_SHOP}")
    if values.get("SHOPIFY_SHOP") != TARGET_SHOP:
        errors.append(f"SHOPIFY_SHOP must be {TARGET_SHOP}")
    if values.get("SHOPIFY_SHOP") == CLIENT_SHOP:
        errors.append("refusing the Buttons Bebe Shopify shop")
    if values.get("GORGIAS_SUBDOMAIN", "").strip().lower() == CLIENT_GORGIAS:
        errors.append("refusing the Buttons Bebe Gorgias workspace")
    if values.get("SHOPIFY_MUTATIONS_ENABLED") != "0":
        errors.append("SHOPIFY_MUTATIONS_ENABLED must be 0")
    if values.get("DEMO_FAKE_SERVICES") != "1":
        errors.append("DEMO_FAKE_SERVICES must be 1")
    if "SHOPIFY_ADMIN_API_TOKEN" in values:
        errors.append("SHOPIFY_ADMIN_API_TOKEN is not allowed in the demo profile")

    db_path = values.get("WEBHOOK_DB_PATH", "")
    if db_path != "./data/cute-things-demo-webhook.db":
        errors.append("WEBHOOK_DB_PATH must be ./data/cute-things-demo-webhook.db")

    for key in (
        "DEMO_KB_FIXTURES",
        "DEMO_REDO_FIXTURES",
        "DEMO_GORGIAS_FIXTURES",
        "DEMO_WHATSAPP_FIXTURES",
    ):
        fixture_path = values.get(key, "")
        if not fixture_path or "demo" not in fixture_path.lower() and "fixture" not in fixture_path.lower():
            errors.append(f"{key} must point to a demo fixture path")

    send_url = values.get("WHATSAPP_SEND_URL", "")
    if send_url and not send_url.startswith("http://127.0.0.1:8185/"):
        errors.append("WHATSAPP_SEND_URL must point to the local fake WhatsApp service")
    if values.get("WA_SEND_SECRET") != values.get("DEMO_WA_SEND_SECRET"):
        errors.append("WA_SEND_SECRET must match the local DEMO_WA_SEND_SECRET")
    ticket_base_url = values.get("WHATSAPP_TICKET_BASE_URL", "")
    if not ticket_base_url.startswith("http://127.0.0.1:8100/"):
        errors.append("WHATSAPP_TICKET_BASE_URL must point to the local demo placeholder")
    support_ticket_base_url = values.get("SUPPORT_TICKET_BASE_URL", "")
    if not support_ticket_base_url.startswith("http://127.0.0.1:8100/"):
        errors.append("SUPPORT_TICKET_BASE_URL must point to the local demo placeholder")

    if values.get("GORGIAS_SUBDOMAIN") != "cute-things-demo":
        errors.append("GORGIAS_SUBDOMAIN must be cute-things-demo")
    if values.get("GORGIAS_BASE_URL") != "http://127.0.0.1:8190":
        errors.append("GORGIAS_BASE_URL must point to the local fake Gorgias service")
    if values.get("DASHBOARD_RESULT_URL") != (
        "http://127.0.0.1:8100/dashboard/api/results"
    ):
        errors.append("DASHBOARD_RESULT_URL must point to the local demo webhook")
    if values.get("FEEDBACK_KB_ROOT") != "./demo/data/kb":
        errors.append("FEEDBACK_KB_ROOT must be ./demo/data/kb")
    if values.get("HERMES_PROFILE") != "cutethingsdemo":
        errors.append("HERMES_PROFILE must be cutethingsdemo")
    if values.get("HERMES_IGNORE_RULES") != "1":
        errors.append("HERMES_IGNORE_RULES must be 1")
    if not values.get("WEBHOOK_SECRET", "").startswith("demo-"):
        errors.append("WEBHOOK_SECRET must be a demo-only value")

    return errors


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("env_file", type=pathlib.Path)
    args = parser.parse_args(argv)

    try:
        values = load_env(args.env_file)
    except (OSError, ValueError) as exc:
        print(f"demo config invalid: {exc}", file=sys.stderr)
        return 2

    errors = validate(values)
    if errors:
        print("Cute Things demo config: BLOCKED")
        for error in errors:
            print(f"- {error}")
        return 1

    print("Cute Things demo config: OK")
    print(f"- shop: {TARGET_SHOP}")
    print(f"- mutations: {values['SHOPIFY_MUTATIONS_ENABLED']}")
    print(f"- queue db: {values['WEBHOOK_DB_PATH']}")
    print(f"- ticket source: {values['GORGIAS_BASE_URL']} (local simulator)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
