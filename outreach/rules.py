"""Pure decision logic: which rows may be sent, which one goes next, and whether to send now.

Nothing here touches the network, so it is fully unit-testable.
"""

from __future__ import annotations

import math
import random
import re
from dataclasses import dataclass, field
from datetime import date, datetime, timedelta

EMAIL_RE = re.compile(r"^[A-Za-z0-9._%+'\-]+@[A-Za-z0-9.\-]+\.[A-Za-z]{2,}$")
SENT_RE = re.compile(r"^\s*(sent|sending)\b", re.I)
DATE_RE = re.compile(r"(\d{4}-\d{2}-\d{2})(?:[ T](\d{2}:\d{2}))?")
FREE_MAIL = {
    "gmail.com", "yahoo.com", "yahoo.co.in", "hotmail.com", "outlook.com", "live.com",
    "rediffmail.com", "icloud.com", "aol.com", "protonmail.com", "zoho.com",
}
YES = {"yes", "y", "approved", "approve", "true", "1"}


# ── small helpers ────────────────────────────────────────────────────────────

def cell(row: list, idx: int) -> str:
    return str(row[idx]).strip() if idx < len(row) and row[idx] is not None else ""


def norm_email(value: str) -> str:
    return value.strip().lower()


def domain_of(email: str) -> str:
    return email.rsplit("@", 1)[-1].lower() if "@" in email else ""


def company_key(email: str) -> str:
    """Companies are grouped by email domain (free-mail addresses are their own group)."""
    d = domain_of(email)
    return email.lower() if d in FREE_MAIL else d


def is_yes(value: str) -> bool:
    return value.strip().lower() in YES


def parse_when(value: str) -> datetime | None:
    m = DATE_RE.search(value or "")
    if not m:
        return None
    try:
        if m.group(2):
            return datetime.strptime(f"{m.group(1)} {m.group(2)}", "%Y-%m-%d %H:%M")
        return datetime.strptime(m.group(1), "%Y-%m-%d")
    except ValueError:
        return None


def first_name(full_name: str) -> str:
    first = (full_name or "").strip().split(" ")[0] if full_name else ""
    if not first or re.fullmatch(r"[A-Z]{1,3}\.?", first):
        return "there"
    return first


# ── data model ───────────────────────────────────────────────────────────────

@dataclass
class Lead:
    source: str          # "contacts" or "bulk"
    row: int             # 1-based sheet row
    email: str
    name: str
    company: str
    tier: str
    status: str          # "valid" or "catchall"
    subject: str
    body: str

    @property
    def key(self) -> str:
        return company_key(self.email)


@dataclass
class Context:
    blocked_emails: set[str] = field(default_factory=set)
    blocked_domains: set[str] = field(default_factory=set)
    sent_log: dict[str, datetime | None] = field(default_factory=dict)        # email -> last sent
    company_sends: dict[str, list[datetime]] = field(default_factory=dict)    # company key -> send times
    replied_companies: set[str] = field(default_factory=set)
    sent_today: int = 0
    catchall_sent_today: int = 0
    last_send_today: datetime | None = None
    last_send_was_catchall: bool = False

    def record_send(self, email: str, when: datetime, status: str, today: date) -> None:
        """Count one send (real, or simulated in test mode) towards today's limits."""
        self.company_sends.setdefault(company_key(email), []).append(when)
        self.sent_log.setdefault(email, when)
        if when.date() == today:
            self.sent_today += 1
            catch = "catch" in status
            if catch:
                self.catchall_sent_today += 1
            if self.last_send_today is None or when >= self.last_send_today:
                self.last_send_today = when
                self.last_send_was_catchall = catch


DRY_STATUS_RE = re.compile(r"\((valid|catchall|catch-all)[,)]", re.I)


def add_dry_runs(ctx: Context, activity: list[list], today: date) -> int:
    """Test mode: treat today's dry-run entries as sends, so the simulation follows the same
    cap, per-company and no-repeat rules as real sending."""
    n = 0
    for r in activity[1:]:
        if cell(r, 1).lower() != "dry-run":
            continue
        when = parse_when(cell(r, 0))
        email = norm_email(cell(r, 2))
        if not when or not email or when.date() != today:
            continue
        m = DRY_STATUS_RE.search(cell(r, 4))
        ctx.record_send(email, when, m.group(1).lower() if m else "valid", today)
        n += 1
    return n


@dataclass
class Settings:
    sending_enabled: bool = False
    daily_cap: int = 15
    catchall_share: float = 0.33
    per_company_per_day: int = 2
    company_gap_days: int = 4
    window_start_hour: int = 11
    window_end_hour: int = 17
    send_on_weekends: bool = False
    dry_run: bool = True

    @classmethod
    def from_rows(cls, rows: list[list]) -> "Settings":
        kv = {cell(r, 0).lower(): cell(r, 1) for r in rows[1:] if cell(r, 0)}
        s = cls()

        def num(key, default, kind):
            try:
                return kind(kv.get(key, "") or default)
            except ValueError:
                return default

        s.sending_enabled = is_yes(kv.get("sending_enabled", "no"))
        s.daily_cap = max(0, num("daily_cap", 15, int))
        s.catchall_share = min(1.0, max(0.0, num("catchall_share", 0.33, float)))
        s.per_company_per_day = max(1, num("per_company_per_day", 2, int))
        s.company_gap_days = max(0, num("company_gap_days", 4, int))
        s.window_start_hour = num("window_start_hour", 11, int)
        s.window_end_hour = num("window_end_hour", 17, int)
        s.send_on_weekends = is_yes(kv.get("send_on_weekends", "no"))
        # Anything other than an explicit "no" keeps test mode on.
        s.dry_run = kv.get("dry_run", "yes").strip().lower() not in {"no", "n", "false", "0"}
        return s


# ── building the context and the candidate list from sheet values ────────────

def build_context(contacts: list[list], bulk: list[list], do_not_email: list[list],
                  sent_log: list[list], today: date) -> Context:
    ctx = Context()
    for r in do_not_email[1:]:
        v = cell(r, 0).lower()
        if not v:
            continue
        if v.startswith("@"):
            ctx.blocked_domains.add(v[1:])
        elif "@" in v:
            ctx.blocked_emails.add(v)
        else:
            ctx.blocked_domains.add(v)

    for r in sent_log[1:]:
        e = norm_email(cell(r, 0))
        if e:
            when = parse_when(cell(r, 1))
            prev = ctx.sent_log.get(e)
            ctx.sent_log[e] = max(filter(None, [prev, when]), default=None)

    def note_send(email: str, sent_cell: str, status: str):
        when = parse_when(sent_cell) or datetime.combine(today, datetime.min.time())
        ctx.record_send(email, when, status, today)

    # Contacts: H=7 email, I=8 status, K=10 sent, L=11 replied
    for r in contacts[1:]:
        e = norm_email(cell(r, 7))
        if not e:
            continue
        if SENT_RE.match(cell(r, 10)):
            note_send(e, cell(r, 10), cell(r, 8).lower())
        if cell(r, 11):
            ctx.replied_companies.add(company_key(e))
    # Bulk list: C=2 email, M=12 sent, N=13 replied, O=14 check
    for r in bulk[1:]:
        e = norm_email(cell(r, 2))
        if not e:
            continue
        if SENT_RE.match(cell(r, 12)):
            note_send(e, cell(r, 12), cell(r, 14).lower())
        if cell(r, 13):
            ctx.replied_companies.add(company_key(e))
    return ctx


def contact_leads(contacts: list[list], companies: list[list]) -> list[Lead]:
    approved = {cell(r, 0): is_yes(cell(r, 9)) for r in companies[1:] if cell(r, 0)}
    leads = []
    for i, r in enumerate(contacts[1:], start=2):
        email = cell(r, 7)
        status = cell(r, 8).lower()
        if not EMAIL_RE.match(email) or status not in {"valid", "catchall"}:
            continue
        if cell(r, 10) or cell(r, 11):                      # already sent / failed / replied
            continue
        subject, body = cell(r, 13), cell(r, 14)
        if not subject or not body or "{first}" in body:
            continue
        if not approved.get(cell(r, 1), False):
            continue
        leads.append(Lead("contacts", i, email, cell(r, 4), cell(r, 2), cell(r, 6).upper(),
                          status, subject, body))
    return leads


def bulk_leads(bulk: list[list]) -> list[Lead]:
    leads = []
    for i, r in enumerate(bulk[1:], start=2):
        email = cell(r, 2)
        check = cell(r, 14).lower()
        if not EMAIL_RE.match(email):
            continue
        if check == "valid":
            status = "valid"
        elif check.startswith("catch"):
            status = "catchall"
        else:
            continue
        if cell(r, 12) or cell(r, 13):
            continue
        subject, body = cell(r, 9), cell(r, 10)
        if not subject or not body or "{first}" in body:
            continue
        if not is_yes(cell(r, 15)):
            continue
        leads.append(Lead("bulk", i, email, cell(r, 1), cell(r, 0), "", status, subject, body))
    return leads


# ── the rules ────────────────────────────────────────────────────────────────

def blocked_reason(lead: Lead, ctx: Context) -> str | None:
    e = lead.email.lower()
    if e in ctx.blocked_emails or domain_of(e) in ctx.blocked_domains:
        return "on Do not email"
    if e in ctx.sent_log:
        return "already emailed"
    if lead.key in ctx.replied_companies:
        return "someone at this company replied"
    return None


def company_allows(lead: Lead, ctx: Context, s: Settings, today: date) -> bool:
    sends = ctx.company_sends.get(lead.key, [])
    today_count = sum(1 for t in sends if t.date() == today)
    if today_count >= s.per_company_per_day:
        return False
    earlier = [t for t in sends if t.date() < today]
    if earlier and (today - max(earlier).date()).days < s.company_gap_days:
        return False
    return True


def catchall_allowance(s: Settings) -> int:
    if s.catchall_share <= 0:
        return 0
    return max(1, math.floor(s.daily_cap * s.catchall_share))


TIER_ORDER = {"A": 0, "B": 1, "C": 2}


def pick_next(leads: list[Lead], ctx: Context, s: Settings, today: date) -> Lead | None:
    eligible = [l for l in leads if blocked_reason(l, ctx) is None and company_allows(l, ctx, s, today)]
    if not eligible:
        return None
    eligible.sort(key=lambda l: (TIER_ORDER.get(l.tier, 3), 0 if l.source == "contacts" else 1, l.row))
    valid = [l for l in eligible if l.status == "valid"]
    catch = [l for l in eligible if l.status == "catchall"]
    # Catch-alls: never past the day's share, never two in a row, and mixed in
    # (roughly every third send) when valid addresses are also waiting.
    catch_ok = bool(catch) and ctx.catchall_sent_today < catchall_allowance(s) \
        and not ctx.last_send_was_catchall
    if catch_ok and (not valid or ctx.sent_today % 3 == 2):
        return catch[0]
    return valid[0] if valid else None


def should_send_now(now: datetime, s: Settings, ctx: Context, run_every_minutes: int = 10,
                    rng: random.Random | None = None) -> tuple[bool, str]:
    rng = rng or random.Random()
    if not s.send_on_weekends and now.weekday() >= 5:
        return False, "weekend"
    start = now.replace(hour=s.window_start_hour, minute=0, second=0, microsecond=0)
    end = now.replace(hour=s.window_end_hour, minute=0, second=0, microsecond=0)
    if not (start <= now < end):
        return False, "outside sending hours"
    remaining = s.daily_cap - ctx.sent_today
    if remaining <= 0:
        return False, f"daily cap reached ({ctx.sent_today}/{s.daily_cap})"
    if ctx.last_send_today and now - ctx.last_send_today < timedelta(minutes=max(6, run_every_minutes - 2)):
        return False, "sent very recently"
    runs_left = max(1, int((end - now).total_seconds() // (run_every_minutes * 60)))
    p = min(1.0, remaining / runs_left)
    if rng.random() < p:
        return True, f"send (chance {p:.0%})"
    return False, f"waiting for a random slot (chance {p:.0%})"
