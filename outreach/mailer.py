"""Build and send one email through GoDaddy SMTP, then file a copy in the Sent folder."""

from __future__ import annotations

import html
import imaplib
import re
import smtplib
import time
from email.message import EmailMessage
from email.utils import formatdate, make_msgid

SIGNATURE = (
    "Thanks & Regards,\n"
    "Ruthvik Anchuri\n"
    "Director | SustainRepo\n"
    "www.sustainrepo.com\n"
    "+91 9642992190"
)
OPT_OUT = "PS: If you'd rather not hear from me again, just reply \"opt out\" and I won't email you again."
FONT = "font-family:-apple-system,BlinkMacSystemFont,'Segoe UI',Roboto,Helvetica,Arial,sans-serif;font-size:14px;line-height:1.6;color:#1f2933"


def build_message(*, sender_email: str, sender_name: str, to_email: str, subject: str, body: str) -> EmailMessage:
    body = body.strip()
    # Drop a sign-off if a draft already carries one, so the signature never doubles.
    body = re.sub(r"\n\s*(thanks\s*(&|and)\s*regards|best\s+regards|regards),?\s*\n.*$", "", body,
                  flags=re.I | re.S).strip()
    plain = f"{body}\n\n{SIGNATURE}\n\n{OPT_OUT}"

    def to_html(text: str) -> str:
        return html.escape(text).replace("\n", "<br>\n")

    html_body = (
        "<!DOCTYPE html><html><head><meta charset=\"utf-8\"></head>"
        f"<body style=\"{FONT}\">"
        f"<div>{to_html(body)}</div>"
        f"<div style=\"margin-top:20px\">{to_html(SIGNATURE).replace('www.sustainrepo.com', '<a href=\"https://www.sustainrepo.com\">www.sustainrepo.com</a>')}</div>"
        f"<div style=\"margin-top:24px;font-size:12px;color:#6b7280\">{to_html(OPT_OUT)}</div>"
        "</body></html>"
    )

    msg = EmailMessage()
    msg["From"] = f"{sender_name} <{sender_email}>"
    msg["To"] = to_email
    msg["Subject"] = subject.strip()
    msg["Date"] = formatdate(localtime=True)
    msg["Message-ID"] = make_msgid(domain=sender_email.split("@")[-1])
    msg["List-Unsubscribe"] = f"<mailto:{sender_email}?subject=unsubscribe>"
    msg.set_content(plain)
    msg.add_alternative(html_body, subtype="html")
    return msg


def send(msg: EmailMessage, *, host: str, port: int, user: str, password: str) -> None:
    with smtplib.SMTP(host, port, timeout=30) as s:
        s.ehlo()
        s.starttls()
        s.ehlo()
        s.login(user, password)
        s.send_message(msg)


def find_sent_folder(imap: imaplib.IMAP4_SSL) -> str:
    status, folders = imap.list()
    if status == "OK":
        for raw in folders or []:
            line = raw.decode(errors="ignore") if isinstance(raw, bytes) else str(raw)
            if "\\sent" in line.lower():
                m = re.search(r'"([^"]+)"\s*$', line) or re.search(r"(\S+)\s*$", line)
                if m:
                    return m.group(1)
    return "Sent"


def file_in_sent(msg: EmailMessage, *, host: str, user: str, password: str) -> None:
    """Best effort: GoDaddy SMTP does not keep a copy, so append one to the Sent folder."""
    try:
        with imaplib.IMAP4_SSL(host, 993, timeout=20) as imap:
            imap.login(user, password)
            folder = find_sent_folder(imap)
            imap.append(f'"{folder}"', "\\Seen", imaplib.Time2Internaldate(time.time()), msg.as_bytes())
            imap.logout()
    except Exception as e:  # noqa: BLE001 - never fail a send over the copy
        print(f"note: could not file a copy in Sent: {e}")
