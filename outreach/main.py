"""Entry point for the Render cron job (runs every 10 minutes).

Each run:
  1. reads the sheet,
  2. about once an hour, reads the mailbox: copies Sent-folder recipients to the Sent log and
     records replies, bounces and opt-outs,
  3. decides at random whether this run sends one email, picks the next approved row by the
     rules, sends it (or only logs it in dry-run mode) and marks it sent.
"""

from __future__ import annotations

import sys
import traceback
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

from . import inbox, mailer, rules
from .config import load_config
from .sheet import Sheet

INBOX_EVERY = timedelta(minutes=55)


def now_ist(tz: str) -> datetime:
    return datetime.now(ZoneInfo(tz)).replace(tzinfo=None)


def fmt(dt: datetime | None) -> str:
    return dt.strftime("%Y-%m-%d %H:%M") if dt else ""


def setting(rows: list[list[str]], name: str) -> str:
    for r in rows[1:]:
        if r and r[0].strip().lower() == name:
            return r[1].strip() if len(r) > 1 else ""
    return ""


def contacted_addresses(data: dict) -> set[str]:
    out = {rules.norm_email(rules.cell(r, 0)) for r in data["sent_log"][1:] if rules.cell(r, 0)}
    out |= {rules.norm_email(rules.cell(r, 7)) for r in data["contacts"][1:] if rules.SENT_RE.match(rules.cell(r, 10))}
    out |= {rules.norm_email(rules.cell(r, 2)) for r in data["bulk"][1:] if rules.SENT_RE.match(rules.cell(r, 12))}
    out.discard("")
    return out


def sync_mailbox(sheet: Sheet, cfg, data: dict, now: datetime, log: list[str]) -> None:
    settings_rows = data["settings"]
    last = rules.parse_when(setting(settings_rows, "last_inbox_check"))
    if last and now - last < INBOX_EVERY:
        return

    # Sent folder -> Sent log (first run imports the full history).
    sent_uid = setting(settings_rows, "sent_last_uid")
    records, newest_sent = inbox.read_sent(cfg.imap_host, cfg.sender_email, cfg.sender_password,
                                           int(sent_uid) if sent_uid.isdigit() else None)
    have = {(rules.norm_email(rules.cell(r, 0)), rules.cell(r, 1)[:10], rules.cell(r, 2)) for r in data["sent_log"][1:]}
    new_rows = []
    for rec in records:
        when = rec.when.astimezone(ZoneInfo(cfg.timezone)).replace(tzinfo=None) if rec.when and rec.when.tzinfo else rec.when
        for addr in rec.recipients:
            key = (addr, fmt(when)[:10], rec.subject)
            if key not in have:
                have.add(key)
                new_rows.append([addr, fmt(when), rec.subject, "GoDaddy Sent folder"])
    sheet.append("sent_log", new_rows)
    if newest_sent:
        sheet.set_setting(settings_rows, "sent_last_uid", str(newest_sent))
    if new_rows:
        log.append(f"{len(new_rows)} Sent-folder recipients added to the Sent log")
        data["sent_log"] += new_rows

    # Inbox -> replies, bounces, opt-outs.
    inbox_uid = setting(settings_rows, "inbox_last_uid")
    contacted = contacted_addresses(data)
    events, newest_in = inbox.read_inbox(cfg.imap_host, cfg.sender_email, cfg.sender_password,
                                         int(inbox_uid) if inbox_uid.isdigit() else None, contacted)
    blocked = {rules.cell(r, 0).lower() for r in data["do_not_email"][1:]}
    activity, dne = [], []
    for ev in events:
        for addr in ev.emails:
            company = rules.company_key(addr)
            activity.append([fmt(now), ev.kind, addr, company, f"{ev.subject[:120]} | {ev.detail}"])
            if ev.kind in ("bounce", "opt-out") and addr not in blocked:
                blocked.add(addr)
                dne.append([addr, "bounced" if ev.kind == "bounce" else "opted out", fmt(now)[:10],
                            ev.subject[:120], "sender"])
            if ev.kind in ("reply", "opt-out"):
                mark_replied(sheet, data, addr, now)
    sheet.append("do_not_email", dne)
    sheet.append("activity", activity)
    if newest_in:
        sheet.set_setting(settings_rows, "inbox_last_uid", str(newest_in))
    sheet.set_setting(settings_rows, "last_inbox_check", fmt(now))
    if events:
        log.append(f"inbox: {len(events)} events ({', '.join(sorted({e.kind for e in events}))})")


def mark_replied(sheet: Sheet, data: dict, addr: str, now: datetime) -> None:
    stamp = f"Yes ({fmt(now)[:10]})"
    for i, r in enumerate(data["contacts"][1:], start=2):
        if rules.norm_email(rules.cell(r, 7)) == addr and not rules.cell(r, 11):
            sheet.set_cell("contacts", f"L{i}", stamp)
    for i, r in enumerate(data["bulk"][1:], start=2):
        if rules.norm_email(rules.cell(r, 2)) == addr and not rules.cell(r, 13):
            sheet.set_cell("bulk", f"N{i}", stamp)


def send_one(sheet: Sheet, cfg, data: dict, settings: rules.Settings, now: datetime, log: list[str]) -> None:
    ctx = rules.build_context(data["contacts"], data["bulk"], data["do_not_email"], data["sent_log"], now.date())
    if not settings.sending_enabled:
        log.append("sending paused (sending_enabled = no)")
        return
    go, why = rules.should_send_now(now, settings, ctx)
    if not go:
        log.append(f"no send: {why}")
        return
    leads = rules.contact_leads(data["contacts"], data["companies"]) + rules.bulk_leads(data["bulk"])
    lead = rules.pick_next(leads, ctx, settings, now.date())
    if not lead:
        log.append(f"no send: nothing approved and eligible ({len(leads)} approved rows checked)")
        return

    tab, email_col, sent_col = ("contacts", "H", "K") if lead.source == "contacts" else ("bulk", "C", "M")
    # Guard against the row having moved or been sent by an overlapping run.
    if rules.norm_email(sheet.cell(tab, f"{email_col}{lead.row}")) != lead.email.lower() \
            or sheet.cell(tab, f"{sent_col}{lead.row}").strip():
        log.append(f"no send: row {lead.row} on {tab} changed since it was read")
        return

    body = lead.body
    msg = mailer.build_message(sender_email=cfg.sender_email, sender_name=cfg.sender_name,
                               to_email=lead.email, subject=lead.subject, body=body)
    if settings.dry_run:
        sheet.append("activity", [[fmt(now), "dry-run", lead.email, lead.company,
                                   f"would send '{lead.subject}' ({lead.status}, {tab} row {lead.row})"]])
        log.append(f"dry run: would send to {lead.email}")
        return

    # Mark first, so a crash after sending can never cause a second send.
    sheet.set_cell(tab, f"{sent_col}{lead.row}", f"sending ({fmt(now)})")
    try:
        mailer.send(msg, host=cfg.smtp_host, port=cfg.smtp_port, user=cfg.sender_email, password=cfg.sender_password)
    except Exception as e:  # noqa: BLE001
        sheet.set_cell(tab, f"{sent_col}{lead.row}", f"failed ({str(e)[:120]})")
        sheet.append("activity", [[fmt(now), "failed", lead.email, lead.company, str(e)[:300]]])
        log.append(f"send failed to {lead.email}: {e}")
        return
    sheet.set_cell(tab, f"{sent_col}{lead.row}", f"sent ({fmt(now)})")
    sheet.append("sent_log", [[lead.email.lower(), fmt(now), lead.subject, "sender"]])
    sheet.append("activity", [[fmt(now), "sent", lead.email, lead.company,
                               f"{lead.subject} ({lead.status}, {tab} row {lead.row})"]])
    mailer.file_in_sent(msg, host=cfg.imap_host, user=cfg.sender_email, password=cfg.sender_password)
    log.append(f"sent to {lead.email}")


def run() -> int:
    cfg = load_config()
    sheet = Sheet(cfg.sheet_id, cfg.service_account_info)
    now = now_ist(cfg.timezone)
    data = sheet.read_all()
    settings = rules.Settings.from_rows(data["settings"])
    log: list[str] = []
    status = 0
    try:
        try:
            sync_mailbox(sheet, cfg, data, now, log)
        except Exception as e:  # noqa: BLE001 - a mailbox hiccup must not stop sending
            log.append(f"mailbox check failed: {e}")
            traceback.print_exc()
        send_one(sheet, cfg, data, settings, now, log)
    except Exception as e:  # noqa: BLE001
        log.append(f"error: {e}")
        traceback.print_exc()
        status = 1
    summary = f"{fmt(now)} IST: " + ("; ".join(log) or "nothing to do")
    print(summary)
    try:
        sheet.set_setting(data["settings"], "last_run", summary[:400])
    except Exception:  # noqa: BLE001
        traceback.print_exc()
    return status


if __name__ == "__main__":
    sys.exit(run())
