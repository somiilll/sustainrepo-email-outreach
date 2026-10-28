# SustainRepo outreach sender

A small Render cron job that sends the approved, verified outreach emails from the
"SustainRepo Outreach Tracker" Google Sheet through the GoDaddy mailbox, one at a time at
random moments during the day, and reports back to the sheet.

The daily Claude run finds people, checks addresses and writes the emails. This sender only sends
what the sheet says is ready and approved. Everything is controlled from the sheet (or the dashboard,
which edits the same sheet).

## What each run does (every 10 minutes, Monday to Friday)

1. Reads the sheet.
2. About once an hour, reads the mailbox:
   - copies every recipient in the GoDaddy Sent folder to the **Sent log** tab (first run imports the full history);
   - finds replies (marks **Replied**), opt-outs and bounces (adds them to **Do not email**), and ignores auto-replies.
3. Decides at random whether this run sends, so sends spread naturally over the day and reach the daily cap.
4. Picks the next row that is approved, verified (valid or catch-all), drafted, not already emailed, not on
   Do not email, and whose company has not replied, following these rules from the **Settings** tab:
   daily cap, catch-all share (never two catch-alls in a row), at most 2 people per company per day,
   then a 4-day gap before more people at that company.
5. Sends it (plain text + HTML, signature, opt-out line, List-Unsubscribe header, no attachment),
   marks the row "sent (date time)", adds it to the Sent log and Activity tabs, and files a copy in the Sent folder.

With `dry_run = yes` (the default), step 5 only writes what it *would* send to the Activity tab.

## Settings tab

| Setting | Meaning |
|---|---|
| sending_enabled | yes / no. The master switch. |
| daily_cap | Most emails per day (plan: 15, then 20, then 30). |
| catchall_share | Largest share of a day's sends that may be catch-all addresses. |
| per_company_per_day | Most people at one company on one day. |
| company_gap_days | Days before more people at an already-contacted company. |
| window_start_hour / window_end_hour | Sending hours, IST. |
| send_on_weekends | yes / no. |
| dry_run | yes = log only, no = really send. |

## Deploy on Render

1. Render dashboard -> New -> Blueprint -> pick this repository (it reads `render.yaml`).
2. When asked for the secret values:
   - `SMTP_PASSWORD`: the GoDaddy mailbox password (type it yourself; it is stored only in Render).
   - `GOOGLE_SERVICE_ACCOUNT_JSON`: the full contents of the service account key file (see below).
3. Share the Google Sheet with the service account's email address as **Editor**.
4. Watch the first runs in Render's logs and in the sheet's **Activity** tab and Settings `last_run`.

### Google service account (one time, about 5 minutes)

1. console.cloud.google.com -> create a project (e.g. "sustainrepo-outreach").
2. APIs & Services -> Library -> enable **Google Sheets API**.
3. APIs & Services -> Credentials -> Create credentials -> Service account -> any name -> Done.
4. Open the service account -> Keys -> Add key -> JSON. A file downloads; paste its whole contents into
   `GOOGLE_SERVICE_ACCOUNT_JSON` on Render, then delete the file.
5. Copy the service account's email (ends in `iam.gserviceaccount.com`) and share the sheet with it as Editor.

## Tests

    pip install -r requirements.txt pytest
    python -m pytest -q
