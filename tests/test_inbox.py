from email.message import EmailMessage

from outreach import inbox, mailer

ME = "ruthvikanchuri@sustainrepo.com"
CONTACTED = {"iti.goyal@shardamotor.com", "neha.singh@sterlingwilson.com", "ompal.prajapati@agigreenpac.com"}


def msg(frm, subject, body, **headers):
    m = EmailMessage()
    m["From"] = frm
    m["To"] = ME
    m["Subject"] = subject
    m["Date"] = "Mon, 02 Nov 2026 12:00:00 +0530"
    for k, v in headers.items():
        m[k.replace("_", "-")] = v
    m.set_content(body)
    return m


def test_reply_from_contacted_person():
    ev = inbox.classify(msg("Iti Goyal <iti.goyal@shardamotor.com>", "Re: BRSR Core assurance",
                            "Thanks, let's talk on Thursday.\n\nOn Mon, Ruthvik wrote:\n> opt out text in quote"),
                        CONTACTED, ME)
    assert ev.kind == "reply" and ev.emails == ["iti.goyal@shardamotor.com"]


def test_opt_out_reply():
    ev = inbox.classify(msg("neha.singh@sterlingwilson.com", "Re: Supply-chain emissions", "Please opt out.\n"),
                        CONTACTED, ME)
    assert ev.kind == "opt-out"


def test_auto_reply_is_not_a_reply():
    ev = inbox.classify(msg("neha.singh@sterlingwilson.com", "Automatic reply: Supply-chain emissions",
                            "I am out of office"), CONTACTED, ME)
    assert ev.kind == "auto-reply"
    ev = inbox.classify(msg("neha.singh@sterlingwilson.com", "Re: hello", "away", Auto_Submitted="auto-replied"),
                        CONTACTED, ME)
    assert ev.kind == "auto-reply"


def test_bounce_names_the_failed_recipient():
    ev = inbox.classify(msg("Mail Delivery System <MAILER-DAEMON@secureserver.net>", "Undelivered Mail Returned to Sender",
                            "This is the mail system.\n<ompal.prajapati@agigreenpac.com>: host mx said: 550 5.1.1 User unknown\n"
                            f"From: {ME}"), CONTACTED, ME)
    assert ev.kind == "bounce" and ev.emails == ["ompal.prajapati@agigreenpac.com"]


def test_strangers_and_newsletters_are_ignored():
    assert inbox.classify(msg("news@vendor.com", "Our offer", "Buy now"), CONTACTED, ME) is None
    assert inbox.classify(msg(ME, "note to self", "x"), CONTACTED, ME) is None


def test_built_message_has_text_html_signature_optout_and_unsubscribe():
    m = mailer.build_message(sender_email=ME, sender_name="Ruthvik Anchuri", to_email="iti.goyal@shardamotor.com",
                             subject="BRSR Core assurance", body="Hi Iti,\n\nBody line.\n\nThanks & Regards,\nRuthvik Anchuri")
    assert m["List-Unsubscribe"] == f"<mailto:{ME}?subject=unsubscribe>"
    plain = m.get_body(("plain",)).get_content()
    html = m.get_body(("html",)).get_content()
    assert plain.count("Thanks & Regards") == 1 and "Director | SustainRepo" in plain and "opt out" in plain
    assert "<html>" in html and "Body line." in html
    assert list(m.iter_attachments()) == []
