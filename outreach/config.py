"""Environment configuration. Secrets come only from environment variables (Render secrets)."""

import json
import os
from dataclasses import dataclass


@dataclass(frozen=True)
class Config:
    sheet_id: str
    service_account_info: dict
    smtp_host: str
    smtp_port: int
    imap_host: str
    sender_email: str
    sender_name: str
    sender_password: str
    timezone: str


def load_config() -> Config:
    raw_sa = os.environ.get("GOOGLE_SERVICE_ACCOUNT_JSON", "").strip()
    if not raw_sa:
        raise RuntimeError("GOOGLE_SERVICE_ACCOUNT_JSON is not set")
    password = os.environ.get("SMTP_PASSWORD", "")
    if not password:
        raise RuntimeError("SMTP_PASSWORD is not set")
    return Config(
        sheet_id=os.environ.get("SHEET_ID", "1zf7xzrKsmYPhGY702H8T1msu8DUhjRq2UFCSM_xfQmY"),
        service_account_info=json.loads(raw_sa),
        smtp_host=os.environ.get("SMTP_HOST", "smtpout.secureserver.net"),
        smtp_port=int(os.environ.get("SMTP_PORT", "587")),
        imap_host=os.environ.get("IMAP_HOST", "imap.secureserver.net"),
        sender_email=os.environ.get("SENDER_EMAIL", "ruthvikanchuri@sustainrepo.com"),
        sender_name=os.environ.get("SENDER_NAME", "Ruthvik Anchuri"),
        sender_password=password,
        timezone=os.environ.get("TIMEZONE", "Asia/Kolkata"),
    )
