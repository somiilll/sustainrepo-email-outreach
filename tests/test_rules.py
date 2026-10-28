import random
from datetime import date, datetime, timedelta

from outreach import rules

TODAY = date(2026, 11, 2)  # a Monday

CONTACT_HDR = ["Batch date", "Rank", "Company", "Symbol", "Name", "Title", "Tier", "Email", "Email status",
               "LinkedIn", "Email sent", "Replied", "Apollo ID", "Email subject", "Email message"]


def crow(rank, name, email, status, tier="A", sent="", replied="", subject="Subj", body="Hi X,\n\nBody"):
    return ["2026-10-20", rank, "Co" + rank, "SYM", name, "Title", tier, email, status, "", sent, replied, "id",
            subject, body]


def companies(*approved_ranks, hold=()):
    rows = [["Rank", "Company", "Symbol", "Status", "Updated", "Note", "G", "H", "I", "Approved"]]
    for r in approved_ranks:
        rows.append([r, "Co" + r, "", "", "", "", "", "S", "B", "Yes"])
    for r in hold:
        rows.append([r, "Co" + r, "", "", "", "", "", "S", "B", "Hold"])
    return rows


BULK_HDR = [["Company", "Contact name", "Email"] + [""] * 13]


def bulk_row(company, name, email, check, approved="Yes", sent="", replied=""):
    return [company, name, email, "", "", "", "", "", "", "Subject", "Hi Y,\n\nBody", "2026-10-29",
            sent, replied, check, approved]


def ctx_for(contacts, bulk=None, dne=None, sent_log=None):
    return rules.build_context([CONTACT_HDR] + contacts, BULK_HDR + (bulk or []),
                               [["Email"]] + (dne or []), [["Email"]] + (sent_log or []), TODAY)


def test_only_approved_verified_unsent_rows_with_drafts_are_candidates():
    contacts = [
        crow("1", "Ann Lee", "ann@a.com", "valid"),
        crow("2", "Bob", "bob@b.com", "invalid"),
        crow("3", "Cy", "cy@c.com", "valid"),            # company on hold
        crow("1", "Di", "di@a.com", "verified"),          # not checked by EmailVerifier yet
        crow("1", "Ed", "ed@a.com", "valid", sent="sent (2026-10-30 11:00)"),
        crow("1", "Flo", "flo@a.com", "catchall", body=""),  # no draft
    ]
    leads = rules.contact_leads([CONTACT_HDR] + contacts, companies("1", "2", hold=("3",)))
    assert [l.email for l in leads] == ["ann@a.com"]


def test_bulk_needs_check_approval_and_draft():
    rows = [
        bulk_row("X", "Xa", "xa@x.com", "valid"),
        bulk_row("X", "Xb", "xb@x.com", "catch-all"),
        bulk_row("Y", "Ya", "ya@y.com", "invalid (mailbox_not_found)"),
        bulk_row("Z", "Za", "za@z.com", "valid", approved=""),
        bulk_row("W", "Wa", "wa@w.com", "already emailed (2025-01-01)"),
    ]
    leads = rules.bulk_leads(BULK_HDR + rows)
    assert [(l.email, l.status) for l in leads] == [("xa@x.com", "valid"), ("xb@x.com", "catchall")]


def test_blocked_and_already_emailed_and_replied_company_are_skipped():
    contacts = [crow("1", "Ann", "ann@a.com", "valid"), crow("2", "Bo", "bo@b.com", "valid"),
                crow("3", "Cy", "cy@c.com", "valid"), crow("4", "Di", "di@d.com", "valid"),
                crow("4", "Dee", "dee@d.com", "valid", sent="sent (2026-10-20 12:00)", replied="Yes")]
    ctx = ctx_for(contacts, dne=[["ann@a.com", "bounced"], ["@b.com", "blocked"]], sent_log=[["cy@c.com", "2025-06-01"]])
    leads = rules.contact_leads([CONTACT_HDR] + contacts, companies("1", "2", "3", "4"))
    assert rules.pick_next(leads, ctx, rules.Settings(), TODAY) is None


def test_two_per_company_per_day_then_four_day_gap():
    s = rules.Settings(per_company_per_day=2, company_gap_days=4)
    contacts = [crow("1", f"P{i}", f"p{i}@a.com", "valid") for i in range(5)]
    sent_today = [crow("1", "S1", "s1@a.com", "valid", sent="sent (2026-11-02 11:10)"),
                  crow("1", "S2", "s2@a.com", "valid", sent="sent (2026-11-02 12:10)")]
    ctx = ctx_for(contacts + sent_today)
    leads = rules.contact_leads([CONTACT_HDR] + contacts, companies("1"))
    assert rules.pick_next(leads, ctx, s, TODAY) is None          # 2 already today

    two_days_ago = [crow("1", "S1", "s1@a.com", "valid", sent="sent (2026-10-31 11:10)")]
    ctx = ctx_for(contacts + two_days_ago)
    assert rules.pick_next(leads, ctx, s, TODAY) is None          # inside the 4-day gap

    five_days_ago = [crow("1", "S1", "s1@a.com", "valid", sent="sent (2026-10-28 11:10)")]
    ctx = ctx_for(contacts + five_days_ago)
    assert rules.pick_next(leads, ctx, s, TODAY) is not None


def test_catchall_share_and_never_two_in_a_row():
    s = rules.Settings(daily_cap=15, catchall_share=0.33)
    assert rules.catchall_allowance(s) == 4
    pending = [crow("1", "V", "v@a.com", "valid"), crow("2", "C", "c@b.com", "catchall")]
    leads = rules.contact_leads([CONTACT_HDR] + pending, companies("1", "2"))
    # last send was a catch-all -> must pick the valid one
    prior = [crow("9", "Z", "z@z.com", "catchall", sent="sent (2026-11-02 11:00)")]
    ctx = ctx_for(pending + prior)
    assert rules.pick_next(leads, ctx, s, TODAY).email == "v@a.com"
    # share used up -> never a catch-all
    used = [crow(str(10 + i), "Z", f"z{i}@z{i}.com", "catchall", sent=f"sent (2026-11-02 1{i}:00)") for i in range(4)]
    ctx = ctx_for(pending + used)
    ctx.last_send_was_catchall = False
    assert rules.pick_next(leads, ctx, s, TODAY).email == "v@a.com"
    # only catch-alls left and allowed -> picks it
    only_c = rules.contact_leads([CONTACT_HDR] + [pending[1]], companies("2"))
    assert rules.pick_next(only_c, ctx_for([pending[1]]), s, TODAY).email == "c@b.com"


def test_tier_a_before_b_and_contacts_before_bulk():
    contacts = [crow("1", "B", "b@a.com", "valid", tier="B"), crow("2", "A", "a@b.com", "valid", tier="A")]
    bulk = [bulk_row("Q", "Q", "q@q.com", "valid")]
    leads = rules.contact_leads([CONTACT_HDR] + contacts, companies("1", "2")) + rules.bulk_leads(BULK_HDR + bulk)
    assert rules.pick_next(leads, ctx_for(contacts, bulk), rules.Settings(), TODAY).email == "a@b.com"


def test_should_send_now_window_cap_weekend_and_gap():
    s = rules.Settings(daily_cap=15)
    ctx = rules.Context()
    always = random.Random(0)
    assert rules.should_send_now(datetime(2026, 11, 1, 12, 0), s, ctx, rng=always)[1] == "weekend"
    assert rules.should_send_now(datetime(2026, 11, 2, 9, 0), s, ctx, rng=always)[1] == "outside sending hours"
    ctx.sent_today = 15
    assert "daily cap" in rules.should_send_now(datetime(2026, 11, 2, 12, 0), s, ctx, rng=always)[1]
    ctx.sent_today, ctx.last_send_today = 3, datetime(2026, 11, 2, 11, 58)
    assert rules.should_send_now(datetime(2026, 11, 2, 12, 0), s, ctx, rng=always)[1] == "sent very recently"
    # near the end of the window with plenty left, it always sends
    ctx.last_send_today = datetime(2026, 11, 2, 15, 0)
    assert rules.should_send_now(datetime(2026, 11, 2, 16, 52), s, ctx, rng=always)[0] is True


def test_daily_volume_lands_near_the_cap():
    """Simulate a day of 10-minute runs: the cap should be reached most days, never exceeded."""
    s = rules.Settings(daily_cap=15)
    totals = []
    for seed in range(200):
        rng = random.Random(seed)
        ctx = rules.Context()
        t = datetime(2026, 11, 2, 11, 0)
        while t.hour < 17:
            go, _ = rules.should_send_now(t, s, ctx, rng=rng)
            if go:
                ctx.sent_today += 1
                ctx.last_send_today = t
            t += timedelta(minutes=10)
        totals.append(ctx.sent_today)
    assert max(totals) <= 15
    assert sum(totals) / len(totals) >= 13


def test_settings_default_to_safe():
    s = rules.Settings.from_rows([["Setting", "Value"]])
    assert s.sending_enabled is False and s.dry_run is True
    s = rules.Settings.from_rows([["Setting", "Value"], ["sending_enabled", "yes"], ["dry_run", "no"], ["daily_cap", "20"]])
    assert s.sending_enabled and not s.dry_run and s.daily_cap == 20


def test_first_name():
    assert rules.first_name("Iti Goyal") == "Iti"
    assert rules.first_name("CS Jain") == "there"
    assert rules.first_name("") == "there"
