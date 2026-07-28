# RE/MAX REC — Automated Meta Lead Follow-up

This robot checks Facebook every 10 minutes for new leads on any of your ad
forms, figures out which agent should get each one, builds the same
branded HTML email your old tool made, and sends it automatically. No more
manually copying leads out of the Leads Center.

## Launching a new campaign? Read this first

**Re-launching an ad for a property/campaign you already have set up** (same
Facebook lead form, just new budget or a new month): do nothing. The robot
matches by the form's name, not by which ad or campaign is spending money on
it, so leads from the relaunched ad flow through automatically.

**Launching an ad for a brand-new property that's never had a form before:**
you need to add exactly one row to the Google Sheet. Takes under a minute,
no code, no GitHub.

1. Open the Google Sheet (bookmark it — the edit link, not the CSV export
   link).
2. Add a new row at the bottom (or higher up, see the ordering note below)
   with:
   - **Match Keyword**: a short piece of text that will appear in the
     Facebook lead form's name for this property. Example: if the form is
     going to be called "Casa en Venta Bosques de las Lomas", a keyword of
     `Bosques de las Lomas` works.
   - **Campaign Label**: the friendly name shown in the email header, e.g.
     "Casa en Venta Bosques de las Lomas".
   - **Property Link**: the SIR listing link, if there is one. Leave blank
     if not applicable.
   - **Recipient Name**: the agent's name, for your own reference.
   - **Recipient Email**: the agent's email — this is where the lead
     actually gets sent.
3. That's it. Save the Sheet (Google Sheets auto-saves). The next check
   (within 10 minutes) will pick up any lead on that form automatically.

**How do I know the keyword will match?** The check is simple: does the
keyword appear anywhere inside the actual Facebook form name, ignoring
capitalization? When in doubt, use a distinctive piece of the property's
address or name rather than a generic word — a keyword like `Casa` would
accidentally match dozens of unrelated forms.

**If you get the keyword wrong or forget to add the row:** nothing breaks
and nobody gets skipped. You'll get an "⚠️ Lead sin campaña asignada" alert
email at the ads mailbox with the lead's full info. Add or fix the row, and
that same lead will go out automatically on the next run — you never need
to resend it by hand.

**Row order matters.** Rows are checked top to bottom and the first keyword
that matches wins. If a new property's name could accidentally overlap with
an existing keyword (e.g. two campaigns both containing "Polanco"), put the
more specific one above the more generic one.

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

## TEST MODE — read this before going live

While the `TEST_MODE` Variable is set to `true` (this is the default), the
robot still does everything for real — reads the Sheet, matches leads,
builds the emails — but every email that would normally go to an agent
(Francisco, Mónica, etc.) is redirected to `TEST_RECIPIENT_EMAIL` instead
(defaults to your ads mailbox), with the subject tagged
`[PRUEBA - iría a agente@email.com]` so you can see exactly who would have
received it. Unmatched-lead alerts already only go to your own inbox, so
those are unaffected either way.

**Do not flip `TEST_MODE` to `false` until you've reviewed a batch of test
emails and are confident the routing and content are correct.** To flip it:
GitHub → this repo → Settings → Secrets and variables → Actions → Variables
tab → edit `TEST_MODE` → `false`.

## Controlling which leads count as "new" (LEADS_SINCE)

Normal scheduled runs only look at leads created on or after the date in the
`LEADS_SINCE` Variable (format `YYYY-MM-DD`). This is what stops an empty
memory file from suddenly treating months of old leads as brand new. Move
this date forward or back any time by editing the Variable — no code
changes needed.

## Sending past leads on purpose (backfill)

Want to (re-)send everything from, say, June 1 to June 15? You don't need a
separate tool — trigger the same workflow manually with a date range:

1. Go to the repo on GitHub → **Actions** tab.
2. Click "Check for new Meta leads and email agents" in the left sidebar.
3. Click "Run workflow" (top right).
4. Fill in **backfill_since** and/or **backfill_until** (format
   `YYYY-MM-DD`). Leave either blank to leave that end open.
5. Click "Run workflow" to confirm.

This ignores `LEADS_SINCE` for that one run and considers every lead in the
date range you gave it (still skipping anything already recorded in
`state.json`, so it's safe to re-run). Do this with `TEST_MODE` still on
first, review what landed in your inbox, and only then flip `TEST_MODE` off
and re-run the same backfill to actually deliver it to agents.

**Want to re-preview leads from a date that's already been handled** (for
example, to see the branded email design for leads that already went out
during setup/testing)? Check the **backfill_force** box when you run the
workflow. Normally the robot never touches a lead twice — this box tells it
"yes, I know, show it to me again anyway." With `TEST_MODE` on, it's
completely safe: the email is still redirected to you, never to a real
agent, no matter how many times you re-run it.

## Running it manually (e.g. to catch up right now)

1. Go to the repo on GitHub.
2. Click the "Actions" tab.
3. Click "Check for new Meta leads and email agents" in the left sidebar.
4. Click "Run workflow" (top right) → leave the backfill fields blank for a
   normal run → "Run workflow" again to confirm.
5. It finishes in under a minute. Click into the run to see exactly what
   it did (how many emails sent, any errors, TEST_MODE status).

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
