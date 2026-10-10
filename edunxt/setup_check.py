"""`python -m edunxt check-setup`: verify each prerequisite and say how to fix what's missing."""

from __future__ import annotations

import secrets

from .config import Settings
from .tools.partners import validate_partners

OK, BAD, WARN = "✅", "❌", "⚠️ "


def check_setup(settings: Settings, send_test_to: str | None = None) -> bool:
    all_ok = True

    def report(ok: bool | None, title: str, detail: str = "") -> None:
        nonlocal all_ok
        mark = OK if ok else (WARN if ok is None else BAD)
        all_ok = all_ok and ok is not False
        print(f"{mark} {title}" + (f"\n     {detail}" if detail else ""))

    # A. Anthropic
    try:
        from .agents.llm import make_client

        client = make_client(settings.anthropic_api_key)
        client.messages.create(
            model=settings.intake_model, max_tokens=16, messages=[{"role": "user", "content": "Reply with: ok"}]
        )
        report(True, f"Anthropic API reachable ({settings.intake_model})")
        client.models.retrieve(settings.research_model)
        report(True, f"Research model available ({settings.research_model})")
    except Exception as exc:
        report(False, "Anthropic API", f"{exc}\n     Fix: set ANTHROPIC_API_KEY (docs/SETUP.md, step A).")

    # B. WhatsApp
    if not settings.whatsapp_configured:
        report(False, "WhatsApp credentials", "Set WA_PHONE_NUMBER_ID and WA_ACCESS_TOKEN (docs/SETUP.md, step B).")
    else:
        from .channels.whatsapp import WhatsAppChannel

        channel = WhatsAppChannel(settings.wa_phone_number_id, settings.wa_access_token, settings.wa_api_version)
        try:
            info = channel.phone_number_info()
            report(True, f"WhatsApp number: {info.get('display_phone_number')} ({info.get('verified_name')})")
        except Exception as exc:
            report(False, "WhatsApp phone number lookup", f"{exc}\n     Fix: check the token hasn't expired and the phone number ID is right.")
        if send_test_to:
            try:
                channel.send_template(send_test_to, "hello_world", "en_US", [])
                report(True, f"Sent hello_world to +{send_test_to}: check that phone")
            except Exception as exc:
                report(False, "Test message", f"{exc}\n     Fix: add +{send_test_to} as a verified recipient in API Setup.")
    report(bool(settings.meta_app_secret) or None, "META_APP_SECRET set", "" if settings.meta_app_secret else "Without it, webhook signatures are not checked (fine locally, required when deployed).")

    # C. Inputs
    if settings.founder_numbers:
        report(True, f"{len(settings.founder_numbers)} founder number(s): " + ", ".join("+" + n for n in settings.founder_numbers))
    else:
        report(False, "FOUNDER_NUMBERS", "Set comma-separated numbers in international format, e.g. +201001234567,+201112345678.")
    if settings.webhook_verify_token:
        report(True, "WEBHOOK_VERIFY_TOKEN set")
    else:
        report(False, "WEBHOOK_VERIFY_TOKEN", f"Pick any random string, e.g. WEBHOOK_VERIFY_TOKEN={secrets.token_urlsafe(24)}")
    problems = validate_partners(settings.partners_csv)
    report(not problems, f"Partner sheet {settings.partners_csv.name}", "; ".join(problems[:5]))

    print("\nAll set!" if all_ok else "\nFix the ❌ items above, then run this again.")
    return all_ok
