"""Settings, read from environment variables (and a local .env file if present)."""

from __future__ import annotations

import os
import re
from dataclasses import dataclass, field
from pathlib import Path

from dotenv import load_dotenv

ROOT = Path(__file__).resolve().parent.parent


def normalize_phone(number: str) -> str:
    """WhatsApp sends numbers as bare digits ("201001234567"); store them the same way."""
    return re.sub(r"\D", "", number or "")


@dataclass
class Settings:
    anthropic_api_key: str = ""
    intake_model: str = "claude-haiku-4-5"
    draft_model: str = "claude-sonnet-5-5"
    draft_effort: str = "medium"
    research_model: str = "claude-opus-5-5"
    research_effort: str = "medium"
    research_max_searches: int = 15
    research_max_fetches: int = 20
    research_fetch_max_tokens: int = 8000
    research_allowed_domains: list[str] = field(default_factory=list)
    list_size: int = 10

    wa_phone_number_id: str = ""
    wa_business_account_id: str = ""
    wa_access_token: str = ""
    wa_api_version: str = "v23.0"
    wa_founder_template: str = "new_case_ready"
    wa_template_language: str = "en"
    meta_app_secret: str = ""
    webhook_verify_token: str = ""

    founder_numbers: list[str] = field(default_factory=list)
    database_url: str = f"sqlite:///{ROOT / 'edunxt.db'}"
    partners_csv: Path = ROOT / "data" / "partners.csv"
    scholarships_csv: Path = ROOT / "data" / "scholarships.csv"
    output_dir: Path = ROOT / "out"

    @property
    def research_log(self) -> Path:
        return self.output_dir / "research_log.jsonl"

    @property
    def whatsapp_configured(self) -> bool:
        return bool(self.wa_phone_number_id and self.wa_access_token)


def load_settings(env_file: str | os.PathLike | None = None) -> Settings:
    load_dotenv(env_file or ROOT / ".env", override=False)
    env = os.environ.get
    return Settings(
        anthropic_api_key=env("ANTHROPIC_API_KEY", ""),
        intake_model=env("INTAKE_MODEL", "claude-haiku-4-5"),
        draft_model=env("DRAFT_MODEL", "claude-sonnet-5-5"),
        draft_effort=env("DRAFT_EFFORT", "medium"),
        research_model=env("RESEARCH_MODEL", "claude-opus-5-5"),
        research_effort=env("RESEARCH_EFFORT", "medium"),
        research_max_searches=int(env("RESEARCH_MAX_SEARCHES", "15")),
        research_max_fetches=int(env("RESEARCH_MAX_FETCHES", "20")),
        research_fetch_max_tokens=int(env("RESEARCH_FETCH_MAX_TOKENS", "8000")),
        research_allowed_domains=[d.strip() for d in env("RESEARCH_ALLOWED_DOMAINS", "").split(",") if d.strip()],
        list_size=int(env("LIST_SIZE", "10")),
        wa_phone_number_id=env("WA_PHONE_NUMBER_ID", ""),
        wa_business_account_id=env("WA_BUSINESS_ACCOUNT_ID", ""),
        wa_access_token=env("WA_ACCESS_TOKEN", ""),
        wa_api_version=env("WA_API_VERSION", "v23.0"),
        wa_founder_template=env("WA_FOUNDER_TEMPLATE", "new_case_ready"),
        wa_template_language=env("WA_TEMPLATE_LANGUAGE", "en"),
        meta_app_secret=env("META_APP_SECRET", ""),
        webhook_verify_token=env("WEBHOOK_VERIFY_TOKEN", ""),
        founder_numbers=[
            normalize_phone(n) for n in env("FOUNDER_NUMBERS", "").split(",") if normalize_phone(n)
        ],
        database_url=env("DATABASE_URL", f"sqlite:///{ROOT / 'edunxt.db'}"),
        partners_csv=Path(env("PARTNERS_CSV", str(ROOT / "data" / "partners.csv"))),
        scholarships_csv=Path(env("SCHOLARSHIPS_CSV", str(ROOT / "data" / "scholarships.csv"))),
        output_dir=Path(env("OUTPUT_DIR", str(ROOT / "out"))),
    )
