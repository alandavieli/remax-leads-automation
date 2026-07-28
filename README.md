# RE/MAX REC — Automated Meta Lead Follow-up

This robot checks Facebook every 10 minutes for new leads on any of your ad
forms, figures out which agent should get each one, builds the same
branded HTML email your old tool made, and sends it automatically. No more
manually copying leads out of the Leads Center.

## The only thing you need to maintain: the Google Sheet

Everything about "who gets what" lives in one Google Sheet — never in code.
Open it any time to add a new campaign, change an email address, or fix a
typo. Changes take effect on the very next check (within 10 minutes), no
deployment, no code, no GitHub needed for this part.

**Columns:**

| Column | What it means |
|---|---|
| Match Keyword | A distinctive piece of text that appears in the Facebook ad form's name. Case doesn't matter. Example: if your form is called "Traspasos de Negocios - agosto 2026", a keyword of `Traspasos de Negocios` will match it (and every future month's version of that same campaign). |
| Campaign Label | The friendly name shown in the email header. |
| Property Link | The SIR property link, if there is one. Leave blank if not applicable — the email just won't show that section. |
| Recipient Name | For your own reference. Not currently used in the email itself. |
| Recipient Email | Where the lead email gets sent. |

**Important:** rows are checked top to bottom, and the first keyword that
matches wins. If you ever have two similar campaigns, put the more
specific one higher up.

**If a lead comes in and nothing matches:** nobody gets skipped silently.
Instead, you (at the ads mailbox) get an email titled "⚠️ Lead sin campaña
asignada" with all the lead's info, telling you to add a row. Once you add
it, that exact lead does NOT need to be resent by hand — just wait for the
next run, or trigger one manually (see below), and it'll go out.

## How the "brain" works (you don't need to touch this)

1. Every 10 minutes, a free GitHub-hosted robot wakes up, reads your Google
   Sheet, and asks Facebook for every lead form on the Page and every lead
   on each form.
2. Any lead it hasn't seen before gets matched against the Sheet by
   keyword, turned into the branded HTML email, and sent from
   `meta_ads@remaxrec.com.mx`.
3. It remembers what it already sent in a small file (`state.json`) so
   nothing is ever emailed twice, even if the robot runs again a minute
   later.

## Running it manually (e.g. to catch up right now)

1. Go to the repo on GitHub.
2. Click the "Actions" tab.
3. Click "Check for new Meta leads and email agents" in the left sidebar.
4. Click "Run workflow" (top right) → "Run workflow" again to confirm.
5. It finishes in under a minute. Click into the run to see exactly what
   it did (how many emails sent, any errors).

## If something looks wrong

Open the Actions tab and click the most recent run — it prints a plain-English
log of every lead it looked at, matched, sent, or alerted on. Common things
to check:

- **A lead didn't get emailed:** check the Actions log for an error near
  that lead's ID. Often it's a typo'd email address in the Sheet.
- **You got an "unmatched" alert you weren't expecting:** someone launched
  a new ad campaign. Add a row to the Sheet with a keyword from that form's
  name.
- **The Facebook token stops working (rare, but can happen if the app is
  ever reset or the connected account changes its password):** a new
  never-expiring token needs to be generated the same way the first one
  was, and pasted into the `FB_PAGE_ACCESS_TOKEN` secret in GitHub
  (Settings → Secrets and variables → Actions).

## Where things live

- **Google Sheet** (routing table, edit anytime): the link is saved in this
  repo's GitHub Variables as `ROUTING_SHEET_CSV_URL` — but you should just
  keep the original edit-link bookmarked, since that CSV link is just a
  read-only mirror of it.
- **Code**: `check_leads.py` — you generally never need to open this.
- **Secrets** (Facebook token, email password): GitHub → this repo →
  Settings → Secrets and variables → Actions. Only paste values there
  directly; never share them in chat or anywhere else.
