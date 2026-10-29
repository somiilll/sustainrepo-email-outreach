"""Read the GoDaddy mailbox: classify inbox mail (reply, bounce, opt-out, auto-reply) and list Sent-folder recipients."""

from __future__ import annotations

import email
import imaplib
import re
from dataclasses import dataclass, field
from datetime import datetime
from email.header import decode_header, make_header
from email.message import Message
from email.utils import getaddresses, parseaddr, parsedate_to_datetime

from .mailer import find_sent_folder

ADDR_RE = re.compile(r"[A-Za-z0-9._%+'\-]+@[A-Za-z0-9.\-]+\.[A-Za-z]{2,}")
BOUNCE_FROM = re.compile(r"mailer-daemon|postmaster|mail delivery|maildelivery|microsoftexchange", re.I)
BOUNCE_SUBJECT = re.compile(
    r"undeliver|delivery status notification|delivery (has )?failed|failure notice|returned mail|"
    r"could not be delivered|mail delivery failed|delivery failure|non[- ]?delivery", re.I)
CALENDAR_SUBJECT = re.compile(
    r"^\s*(invitation|updated invitation|canceled event|cancelled event|accepted|declined|tentatively accepted|"
    r"new event|event (updated|canceled|cancelled))\b", re.I)
NO_REPLY = re.compile(r"^(no[-_.]?reply|do[-_.]?not[-_.]?reply|notifications?|calendar-notification)@", re.I)
AUTO_SUBJECT = re.compile(r"automatic reply|auto[- ]?reply|out of (the )?office|on leave|autoreply", re.I)
OPT_OUT = re.compile(r"\b(opt[\s-]?out|unsubscribe|remove me|stop emailing|do not (email|contact)|"
                     r"not interested,? please remove)\b", re.I)


@dataclass
class InboxEvent:
    kind: str                     # reply | bounce | opt-out | auto-reply
    emails: list[str]             # contacted addresses this event is about
    when: datetime | None
    subject: str
    detail: str = ""


@dataclass
class SentRecord:
    recipients: list[str]
    when: datetime | None
    subject: str


def _decode(value: str | None) -> str:
    if not value:
        return ""
    try:
        return str(make_header(decode_header(value)))
    except Exception:  # noqa: BLE001
        return value


def _text_of(msg: Message, limit: int = 20000) -> str:
    parts = []
    for part in msg.walk():
        ctype = part.get_content_type()
        if ctype in ("text/plain", "message/delivery-status", "text/rfc822-headers") or ctype.startswith("message/"):
            payload = part.get_payload(decode=True)
            if payload is None and part.is_multipart():
                continue
            if payload is None:
                payload = str(part.get_payload()).encode()
            parts.append(payload.decode(part.get_content_charset() or "utf-8", errors="ignore"))
        elif ctype == "text/html" and not parts:
            payload = part.get_payload(decode=True) or b""
            parts.append(re.sub(r"<[^>]+>", " ", payload.decode(part.get_content_charset() or "utf-8", errors="ignore")))
    return "\n".join(parts)[:limit]


def _new_text(body: str) -> str:
    """The part of a reply the person typed: stop at the quoted original."""
    lines = []
    for line in body.splitlines():
        if line.startswith(">") or re.match(r"\s*On .+wrote:\s*$", line) or re.match(r"\s*-{2,}\s*Original Message", line, re.I) \
                or re.match(r"\s*From:\s", line):
            break
        lines.append(line)
    return "\n".join(lines)


def _is_calendar(msg: Message, subject: str) -> bool:
    if CALENDAR_SUBJECT.search(subject):
        return True
    return any(part.get_content_type() == "text/calendar" for part in msg.walk())


def _is_bounce(sender: str, subject: str) -> bool:
    return bool(BOUNCE_FROM.search(sender) or BOUNCE_SUBJECT.search(subject))


def _skip_sender(sender: str, subject: str, outreach: set[str], own_domain: str) -> bool:
    """True when the mail can never be an outreach reply (decided from headers only)."""
    return (sender not in outreach or sender.endswith("@" + own_domain) or bool(NO_REPLY.search(sender))
            or bool(CALENDAR_SUBJECT.search(subject)))


def needs_body(headers: Message, outreach: set[str], own_address: str) -> bool:
    """Decide from the From and Subject headers alone whether the full email must be opened.

    Only bounce notices and mail from people we cold-emailed are opened. Everything else
    (clients, partners, colleagues, newsletters, invites) is never downloaded.
    """
    sender = parseaddr(headers.get("From", ""))[1].lower()
    subject = _decode(headers.get("Subject"))
    own = own_address.lower()
    if sender == own:
        return False
    if _is_bounce(sender, subject):
        return True
    return not _skip_sender(sender, subject, outreach, own.rsplit("@", 1)[-1])


def classify(msg: Message, contacted: set[str], own_address: str,
             bounce_list: set[str] | None = None) -> InboxEvent | None:
    """contacted = people we cold-emailed (outreach); bounce_list = every address we ever emailed."""
    bounce_list = contacted if bounce_list is None else bounce_list
    sender = parseaddr(msg.get("From", ""))[1].lower()
    subject = _decode(msg.get("Subject"))
    try:
        when = parsedate_to_datetime(msg.get("Date")) if msg.get("Date") else None
    except (TypeError, ValueError):
        when = None
    own_domain = own_address.lower().rsplit("@", 1)[-1]
    if sender == own_address.lower():
        return None
    body = _text_of(msg)

    if _is_bounce(sender, subject):
        found = {a.lower() for a in ADDR_RE.findall(body)}
        hit = sorted(a for a in found & bounce_list if not a.endswith("@" + own_domain))
        if hit:
            return InboxEvent("bounce", hit, when, subject, "delivery failure")
        return None

    # Only people we emailed count. Colleagues on our own domain, no-reply senders and
    # calendar invitations are never replies.
    if _skip_sender(sender, subject, contacted, own_domain) or _is_calendar(msg, subject):
        return None
    auto = (msg.get("Auto-Submitted", "").lower() not in ("", "no")) or AUTO_SUBJECT.search(subject) \
        or msg.get("X-Autoreply") or msg.get("X-Autorespond")
    if auto:
        return InboxEvent("auto-reply", [sender], when, subject, "automatic reply (not counted as a reply)")
    typed = _new_text(body)
    if OPT_OUT.search(typed) or OPT_OUT.search(subject):
        return InboxEvent("opt-out", [sender], when, subject, "asked not to be contacted")
    # The reply's text is never stored: only that a reply arrived.
    return InboxEvent("reply", [sender], when, subject, "reply received")


def _uids_after(imap: imaplib.IMAP4_SSL, last_uid: int | None, since_days: int) -> list[int]:
    if last_uid:
        status, data = imap.uid("search", None, f"UID {last_uid + 1}:*")
    else:
        since = (datetime.now().date().toordinal() - since_days)
        since_str = datetime.fromordinal(since).strftime("%d-%b-%Y")
        status, data = imap.uid("search", None, f"SINCE {since_str}")
    if status != "OK" or not data or not data[0]:
        return []
    uids = [int(x) for x in data[0].split()]
    return [u for u in uids if not last_uid or u > last_uid]


HEADER_FIELDS = "(BODY.PEEK[HEADER.FIELDS (FROM SUBJECT DATE)])"


def read_inbox(host: str, user: str, password: str, last_uid: int | None, contacted: set[str],
               first_run_days: int = 21, max_messages: int = 300,
               bounce_list: set[str] | None = None) -> tuple[list[InboxEvent], int | None]:
    """Read new inbox mail read-only. Only From/Subject/Date are fetched for every email; the full
    email is downloaded only for bounce notices and mail from people we cold-emailed."""
    events: list[InboxEvent] = []
    newest = last_uid
    with imaplib.IMAP4_SSL(host, 993, timeout=30) as imap:
        imap.login(user, password)
        imap.select("INBOX", readonly=True)
        uids = _uids_after(imap, last_uid, first_run_days)[-max_messages:]
        for uid in uids:
            newest = max(newest or 0, uid)
            status, data = imap.uid("fetch", str(uid), HEADER_FIELDS)
            if status != "OK" or not data or not isinstance(data[0], tuple):
                continue
            if not needs_body(email.message_from_bytes(data[0][1]), contacted, user):
                continue
            status, data = imap.uid("fetch", str(uid), "(BODY.PEEK[])")
            if status != "OK" or not data or not isinstance(data[0], tuple):
                continue
            msg = email.message_from_bytes(data[0][1])
            ev = classify(msg, contacted, user, bounce_list)
            if ev:
                events.append(ev)
            newest = max(newest or 0, uid)
        imap.logout()
    return events, newest


def read_sent(host: str, user: str, password: str, last_uid: int | None,
              first_run_days: int = 3650, max_messages: int = 2000) -> tuple[list[SentRecord], int | None]:
    records: list[SentRecord] = []
    newest = last_uid
    with imaplib.IMAP4_SSL(host, 993, timeout=30) as imap:
        imap.login(user, password)
        folder = find_sent_folder(imap)
        imap.select(f'"{folder}"', readonly=True)
        uids = _uids_after(imap, last_uid, first_run_days)[-max_messages:]
        for uid in uids:
            status, data = imap.uid("fetch", str(uid), "(BODY.PEEK[HEADER.FIELDS (TO CC BCC DATE SUBJECT)])")
            if status != "OK" or not data or not isinstance(data[0], tuple):
                continue
            hdr = email.message_from_bytes(data[0][1])
            recips = [a.lower() for _, a in getaddresses(hdr.get_all("To", []) + hdr.get_all("Cc", []) + hdr.get_all("Bcc", [])) if a]
            try:
                when = parsedate_to_datetime(hdr.get("Date")) if hdr.get("Date") else None
            except (TypeError, ValueError):
                when = None
            if recips:
                records.append(SentRecord(recips, when, _decode(hdr.get("Subject"))))
            newest = max(newest or 0, uid)
        imap.logout()
    return records, newest
