#!/usr/bin/env python3
"""
RE/MAX REC - Automated Meta Lead Follow-up
============================================

What this does, every time it runs:
1. Reads the routing table (a published Google Sheet) that says which
   Facebook ad form goes to which agent.
2. Asks Facebook for every lead form on the Page, and every lead on each form.
3. For any lead we haven't emailed yet:
   - Matches its ad/form name against the routing table (by keyword).
   - Builds the same branded HTML email Alan's old tool produced.
   - Sends it via the RE/MAX ads mailbox (Migadu SMTP).
   - If NO routing match is found, emails an alert instead, so no lead
     is ever silently lost - it just means someone needs to add a row
     to the Google Sheet.
4. Remembers what it already sent (state.json) so it never double-sends,
   and commits that memory file back to the repo.

Nothing in this file is campaign-specific. All of that lives in the
Google Sheet, which Alan can edit any time without touching this code.
"""

import csv
import io
import json
import os
import re
import smtplib
import sys
import time
from datetime import datetime, timezone
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText
from urllib.parse import quote

import requests

# ---------------------------------------------------------------------------
# Configuration - all secrets/values come from environment variables, which
# are set as GitHub Actions Secrets/Variables. Nothing sensitive is hardcoded.
# ---------------------------------------------------------------------------

FB_PAGE_ID = os.environ["FB_PAGE_ID"].strip()
FB_PAGE_ACCESS_TOKEN = os.environ["FB_PAGE_ACCESS_TOKEN"].strip()
ROUTING_SHEET_CSV_URL = os.environ["ROUTING_SHEET_CSV_URL"].strip()

SMTP_HOST = os.environ.get("SMTP_HOST", "smtp.migadu.com").strip()
SMTP_PORT = int(os.environ.get("SMTP_PORT", "465").strip())
SMTP_USERNAME = os.environ["SMTP_USERNAME"].strip()
SMTP_PASSWORD = os.environ["SMTP_PASSWORD"].strip()
FROM_NAME = os.environ.get("FROM_NAME", "RE/MAX Real Estate Consultants")

# Where "no routing match found" alerts get sent. Defaults to the same
# mailbox the automation sends from, so Alan sees it in the ads inbox.
ALERT_EMAIL = os.environ.get("ALERT_EMAIL", SMTP_USERNAME)

# --- Safety valve: TEST MODE -----------------------------------------------
# While TEST_MODE is true (the default), every email that would normally go
# to a real agent is redirected to TEST_RECIPIENT_EMAIL instead, with a
# "[PRUEBA]" tag on the subject. Unmatched-lead alerts already only go to
# ALERT_EMAIL (Alan's own inbox), so they are left as-is. Nothing reaches a
# real agent's inbox until Alan deliberately sets TEST_MODE to "false" in the
# repo's GitHub Actions Variables.
TEST_MODE = os.environ.get("TEST_MODE", "true").strip().lower() in ("1", "true", "yes")
TEST_RECIPIENT_EMAIL = os.environ.get("TEST_RECIPIENT_EMAIL", "").strip() or ALERT_EMAIL

# Once TEST_MODE is off and emails really go to agents, this address gets a
# silent Bcc on every one of those sends - a visual paper trail in your own
# inbox even though the mail was sent by a script instead of Thunderbird.
# Not applied while TEST_MODE is on, since the email is already landing here
# as the primary recipient in that case.
AGENT_EMAIL_BCC = os.environ.get("AGENT_EMAIL_BCC", "").strip() or SMTP_USERNAME

# --- Which leads count as "in scope" for this run ---------------------------
# Normal scheduled runs only ever look at leads created on/after LEADS_SINCE,
# so an empty state.json (e.g. right after first setup) never causes a flood
# of months-old leads to go out. Set via the LEADS_SINCE repo Variable
# (format: YYYY-MM-DD).
#
# To deliberately reach further back - "send me everything from June 1 to
# June 15" - trigger the workflow manually from the Actions tab and fill in
# the backfill_since / backfill_until inputs. When present, they replace
# LEADS_SINCE entirely for that one run. Leads already recorded in
# state.json are still never re-sent, backfill or not.
LEADS_SINCE = os.environ.get("LEADS_SINCE", "").strip()
BACKFILL_SINCE = os.environ.get("BACKFILL_SINCE", "").strip()
BACKFILL_UNTIL = os.environ.get("BACKFILL_UNTIL", "").strip()

# By default, even a backfill run skips any lead already recorded in
# state.json (safe to re-run without duplicating sends). Set BACKFILL_FORCE
# to re-preview leads that were already processed/alerted earlier - e.g. to
# see what a lead's branded email looks like after the fact. This only
# controls whether old leads are reconsidered; TEST_MODE still decides
# whether the email actually reaches a real agent or gets redirected to you.
BACKFILL_FORCE = os.environ.get("BACKFILL_FORCE", "false").strip().lower() in ("1", "true", "yes")

# When releasing a big batch of backfill leads for real, sending them all in
# one run looks like a burst to the recipient's mail provider and risks spam
# flags. Set this to only send a handful per run - the backfill window stays
# active across runs (including the normal 10-minute scheduled check-ins),
# so the rest trickle out a few at a time until the whole batch is done.
# Leave at 0 (or unset) for no cap - normal backfill behavior.
BACKFILL_MAX_PER_RUN = int((os.environ.get("BACKFILL_MAX_PER_RUN", "0").strip() or "0"))

STATE_FILE = os.path.join(os.path.dirname(__file__), "state.json")

GRAPH_API_VERSION = "v25.0"
GRAPH_BASE = f"https://graph.facebook.com/{GRAPH_API_VERSION}"

CORE_FIELD_KEYS = {"full_name", "first_name", "last_name", "email", "phone_number", "phone"}


def parse_date_boundary(value, end_of_day=False):
    """Parses a YYYY-MM-DD string (from a Variable or workflow input) into
    a timezone-aware datetime. Returns None if value is empty/invalid."""
    if not value:
        return None
    try:
        dt = datetime.strptime(value, "%Y-%m-%d").replace(tzinfo=timezone.utc)
        if end_of_day:
            dt = dt.replace(hour=23, minute=59, second=59)
        return dt
    except ValueError:
        print(f"  ! Ignoring unparseable date '{value}' (expected YYYY-MM-DD)")
        return None


def parse_lead_created_time(created_time):
    """Facebook returns e.g. '2026-07-20T15:04:23+0000'."""
    try:
        return datetime.strptime(created_time, "%Y-%m-%dT%H:%M:%S%z")
    except (TypeError, ValueError):
        return None


# ---------------------------------------------------------------------------
# State (which leads have we already handled)
# ---------------------------------------------------------------------------

def load_state():
    if os.path.exists(STATE_FILE):
        with open(STATE_FILE, "r", encoding="utf-8") as f:
            return json.load(f)
    return {"processed": [], "alerted": []}


def save_state(state):
    with open(STATE_FILE, "w", encoding="utf-8") as f:
        json.dump(state, f, indent=2, ensure_ascii=False)


# ---------------------------------------------------------------------------
# Routing table (Google Sheet, published as CSV, no auth needed)
# ---------------------------------------------------------------------------

def load_routing_table():
    resp = requests.get(ROUTING_SHEET_CSV_URL, timeout=30)
    resp.raise_for_status()
    reader = csv.DictReader(io.StringIO(resp.text))
    rows = []
    for row in reader:
        keyword = (row.get("Match Keyword") or "").strip()
        if not keyword:
            continue
        rows.append({
            "keyword": keyword,
            "campaign_label": (row.get("Campaign Label") or keyword).strip(),
            "property_link": (row.get("Property Link") or "").strip(),
            "recipient_name": (row.get("Recipient Name") or "").strip(),
            "recipient_email": (row.get("Recipient Email") or "").strip(),
        })
    return rows


def match_route(form_name, routing_rows):
    form_name_lower = form_name.lower()
    for row in routing_rows:
        if row["keyword"].lower() in form_name_lower:
            return row
    return None


# ---------------------------------------------------------------------------
# Facebook Graph API
# ---------------------------------------------------------------------------

def fb_get(path, params=None):
    params = dict(params or {})
    params["access_token"] = FB_PAGE_ACCESS_TOKEN
    url = f"{GRAPH_BASE}/{path}"
    all_data = []
    while url:
        resp = requests.get(url, params=params, timeout=30)
        if not resp.ok:
            print(f"Facebook API error ({resp.status_code}) calling {path}:")
            print(resp.text)
        resp.raise_for_status()
        payload = resp.json()
        if isinstance(payload.get("data"), list):
            all_data.extend(payload["data"])
            next_url = payload.get("paging", {}).get("next")
            url = next_url
            params = {}  # next_url already has everything encoded
        else:
            return payload
    return {"data": all_data}


def get_lead_forms():
    result = fb_get(f"{FB_PAGE_ID}/leadgen_forms", {"fields": "id,name,status"})
    return result.get("data", [])


def get_form_questions(form_id):
    result = fb_get(form_id, {"fields": "questions"})
    questions = result.get("questions", [])
    lookup = {}
    for q in questions:
        options = {opt["key"]: opt["value"] for opt in q.get("options", [])}
        lookup[q["key"]] = {"label": q.get("label", q["key"]), "options": options}
    return lookup


def get_leads_for_form(form_id):
    result = fb_get(form_id + "/leads", {"fields": "id,created_time,field_data", "limit": 100})
    return result.get("data", [])


# ---------------------------------------------------------------------------
# Email template (faithfully replicates Alan's original HTML generator)
# ---------------------------------------------------------------------------

EMAIL_STYLE = (
    "body{font-family:Arial,sans-serif;line-height:1.6;color:#333;max-width:600px;"
    "margin:0 auto;padding:20px}"
    "h2{color:#2c3e50;border-bottom:2px solid #3498db;padding-bottom:10px}"
    "h3{color:#2c3e50;margin-top:25px}"
    ".info-box{background-color:#f8f9fa;padding:15px;border-radius:5px;margin:20px 0}"
    "table{width:100%;border-collapse:collapse;margin:15px 0}"
    "td{padding:10px;border:1px solid #dee2e6}"
    ".label{font-weight:bold;background-color:#f8f9fa;width:30%}"
    ".question{background-color:#e8f4f8;padding:10px;margin:10px 0;border-left:4px solid #3498db}"
    ".link{background-color:#d4edda;padding:15px;border-radius:5px;margin-top:20px}"
    ".wa-btn{display:block;margin:20px 0;padding:14px 20px;background-color:#25D366;"
    "color:#ffffff;text-align:center;text-decoration:none;border-radius:8px;"
    "font-weight:bold;font-size:15px}"
    "a{color:#007bff;text-decoration:none}"
)


def esc(value):
    """Minimal HTML-escaping for values we interpolate into the template."""
    if value is None:
        return ""
    return (
        str(value)
        .replace("&", "&amp;")
        .replace("<", "&lt;")
        .replace(">", "&gt;")
    )


def digits_only(phone):
    return re.sub(r"\D", "", phone or "")


def build_email_html(campaign_label, property_link, name, email, phone, qa_pairs):
    wa_number = digits_only(phone)
    wa_block = ""
    if wa_number:
        wa_block = (
            f'<a href="https://wa.me/{wa_number}" class="wa-btn">'
            f"\U0001F4F1 Contactar por WhatsApp — {esc(phone)}</a>"
        )

    questions_html = ""
    for label, answer in qa_pairs:
        questions_html += (
            '<div class="question"><strong>{label}</strong><br>\n'
            "  {answer}</div>\n  \n"
        ).format(label=esc(label), answer=esc(answer))

    link_block = ""
    if property_link:
        link_block = (
            '<div class="link"><strong>Link de la propiedad de interés en el SIR:</strong><br>\n'
            f'  <a href="{esc(property_link)}">{esc(property_link)}</a></div>\n'
        )

    return f"""<!DOCTYPE html>
<html>
<head>
<meta http-equiv="content-type" content="text/html; charset=UTF-8">
</head>
<body>
<p>
<meta charset="UTF-8">
<style>{EMAIL_STYLE}</style>
<h2>Nuevo lead de Meta Ads registrado</h2>
<div class="info-box">
<p style="margin:5px 0"><strong>Campaña:</strong> {esc(campaign_label)}</p>
</div>
<h3>Datos del Lead:</h3>
<table>
<tbody>
<tr>
<td class="label">Nombre:</td>
<td>{esc(name)}</td>
</tr>
<tr>
<td class="label">Email:</td>
<td><a href="mailto:{esc(email)}">{esc(email)}</a></td>
</tr>
<tr>
<td class="label">Teléfono:</td>
<td>{esc(phone)}</td>
</tr>
</tbody>
</table>
{wa_block}
<h3>Respuestas del formulario de filtración:</h3>
{questions_html}
{link_block}
<br>
</p>
</body>
</html>"""


def build_alert_html(form_name, lead_id, created_time, name, email, phone, qa_pairs):
    rows = "".join(
        f"<tr><td class='label'>{esc(label)}</td><td>{esc(answer)}</td></tr>"
        for label, answer in qa_pairs
    )
    return f"""<!DOCTYPE html>
<html><body style="font-family:Arial,sans-serif;max-width:600px;margin:0 auto;padding:20px">
<h2 style="color:#c0392b">⚠️ Lead sin campaña asignada</h2>
<p>Llegó un lead de un formulario que no coincide con ninguna fila de la
hoja de ruteo (Google Sheet). No se envió ningún correo al agente todavía.
Agrega una fila a la hoja con una palabra clave que aparezca en el nombre
del formulario, y este lead se procesará solo en la siguiente ejecución
(no hace falta reenviarlo a mano).</p>
<table style="width:100%;border-collapse:collapse;margin:15px 0">
<tr><td class="label" style="font-weight:bold;background:#f8f9fa;padding:8px;border:1px solid #dee2e6">Formulario</td><td style="padding:8px;border:1px solid #dee2e6">{esc(form_name)}</td></tr>
<tr><td style="font-weight:bold;background:#f8f9fa;padding:8px;border:1px solid #dee2e6">Lead ID</td><td style="padding:8px;border:1px solid #dee2e6">{esc(lead_id)}</td></tr>
<tr><td style="font-weight:bold;background:#f8f9fa;padding:8px;border:1px solid #dee2e6">Fecha</td><td style="padding:8px;border:1px solid #dee2e6">{esc(created_time)}</td></tr>
<tr><td style="font-weight:bold;background:#f8f9fa;padding:8px;border:1px solid #dee2e6">Nombre</td><td style="padding:8px;border:1px solid #dee2e6">{esc(name)}</td></tr>
<tr><td style="font-weight:bold;background:#f8f9fa;padding:8px;border:1px solid #dee2e6">Email</td><td style="padding:8px;border:1px solid #dee2e6">{esc(email)}</td></tr>
<tr><td style="font-weight:bold;background:#f8f9fa;padding:8px;border:1px solid #dee2e6">Teléfono</td><td style="padding:8px;border:1px solid #dee2e6">{esc(phone)}</td></tr>
{rows}
</table>
</body></html>"""


# ---------------------------------------------------------------------------
# Sending mail
# ---------------------------------------------------------------------------

class SmtpClient:
    """Keeps ONE SMTP connection open and reuses it for every email in the
    run, instead of opening a brand-new connection (with its own TLS
    handshake + login) for every single send. Opening dozens of fresh
    connections in a few minutes is exactly the kind of pattern mail
    providers flag as abusive and start dropping ("Connection reset by
    peer") - a single reused connection looks like normal behavior and
    avoids that entirely. If the connection does die mid-run (network
    hiccup, server-side timeout), we transparently reconnect and retry
    once before giving up on that particular email.
    """

    def __init__(self):
        self._server = None

    def _connect(self):
        server = smtplib.SMTP_SSL(SMTP_HOST, SMTP_PORT, timeout=30)
        server.login(SMTP_USERNAME, SMTP_PASSWORD)
        self._server = server

    def _ensure_connected(self):
        if self._server is None:
            self._connect()

    def send(self, to_email, subject, html_body, bcc_email=None):
        msg = MIMEMultipart("alternative")
        msg["Subject"] = subject
        msg["From"] = f"{FROM_NAME} <{SMTP_USERNAME}>"
        msg["To"] = to_email
        # Bcc is intentionally NOT added as a header - it's only used for the
        # envelope recipient list below, so the agent never sees it was copied.
        msg.attach(MIMEText(html_body, "html", "utf-8"))

        envelope_recipients = [to_email]
        if bcc_email and bcc_email != to_email:
            envelope_recipients.append(bcc_email)

        last_error = None
        for attempt in range(2):
            try:
                self._ensure_connected()
                self._server.sendmail(SMTP_USERNAME, envelope_recipients, msg.as_string())
                return
            except Exception as e:
                last_error = e
                # The connection is presumed dead - drop it and force a
                # fresh one on the next attempt (or the next email).
                try:
                    self._server.quit()
                except Exception:
                    pass
                self._server = None
                if attempt == 0:
                    time.sleep(5)
        raise last_error

    def close(self):
        if self._server is not None:
            try:
                self._server.quit()
            except Exception:
                pass
            self._server = None


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def extract_lead_fields(field_data, questions_lookup):
    """Returns (name, email, phone, qa_pairs) from a lead's field_data."""
    raw = {}
    for fd in field_data:
        values = fd.get("values") or [""]
        raw[fd["name"]] = values[0] if values else ""

    name = raw.get("full_name") or " ".join(
        filter(None, [raw.get("first_name"), raw.get("last_name")])
    )
    email = raw.get("email", "")
    phone = raw.get("phone_number") or raw.get("phone", "")

    qa_pairs = []
    for key, value in raw.items():
        if key in CORE_FIELD_KEYS:
            continue
        q_info = questions_lookup.get(key)
        label = q_info["label"] if q_info else key
        if q_info and value in q_info["options"]:
            value = q_info["options"][value]
        qa_pairs.append((label, value))

    return name, email, phone, qa_pairs


def main():
    state = load_state()
    processed = set(state.get("processed", []))
    alerted = set(state.get("alerted", []))

    routing_rows = load_routing_table()
    print(f"Loaded {len(routing_rows)} routing rules from the Google Sheet.")

    # Work out the effective date window(s) for this run. Two windows can be
    # active at the same time:
    # - the LIVE window (LEADS_SINCE): always processed immediately, no cap
    #   - this is what keeps today's real leads flowing out without delay.
    # - the BACKFILL window (BACKFILL_SINCE/UNTIL): a historical range being
    #   deliberately (re-)released. If BACKFILL_MAX_PER_RUN is set, only
    #   that many backfill-window sends happen in this run - the window
    #   stays active across future runs (including the normal 10-minute
    #   scheduled check-ins) so the rest trickle out gradually instead of
    #   landing all at once.
    live_since_dt = parse_date_boundary(LEADS_SINCE)
    if live_since_dt:
        print(f"Live window: considering leads created on/after {LEADS_SINCE} (no cap).")
    else:
        print("Live window: no LEADS_SINCE cutoff set, considering all unprocessed leads (no cap).")

    has_backfill = bool(BACKFILL_SINCE or BACKFILL_UNTIL)
    backfill_since_dt = parse_date_boundary(BACKFILL_SINCE) if has_backfill else None
    backfill_until_dt = parse_date_boundary(BACKFILL_UNTIL, end_of_day=True) if has_backfill else None
    if has_backfill:
        cap_note = (f", throttled to {BACKFILL_MAX_PER_RUN} send(s) this run"
                    if BACKFILL_MAX_PER_RUN else " (no cap)")
        print(f"Backfill window ALSO active: {BACKFILL_SINCE or 'the beginning'} "
              f"to {BACKFILL_UNTIL or 'now'}{cap_note}.")

    print(f"TEST_MODE is {'ON' if TEST_MODE else 'OFF'} "
          f"({'agent emails redirected to ' + TEST_RECIPIENT_EMAIL if TEST_MODE else 'sending to real agents'}).")

    force_resend = has_backfill and BACKFILL_FORCE
    if force_resend:
        print("BACKFILL_FORCE is ON - re-considering leads even if already "
              "processed/alerted before.")

    forms = get_lead_forms()
    print(f"Found {len(forms)} lead forms on the Page.")
    # Diagnostic: list every form this Page token can actually see, so a
    # lead that never gets processed or alerted on can be traced back to
    # whether its form is visible to the API at all (vs. some other bug
    # further down the pipeline).
    for f in forms:
        print(f"   - form {f.get('id')}: {f.get('name', '(no name)')} [{f.get('status', '?')}]")

    smtp = SmtpClient()
    sent_count = 0
    alert_count = 0
    skipped_out_of_range = 0
    throttled_sent = 0
    throttled_deferred = 0

    for form in forms:
        form_id = form["id"]
        form_name = form.get("name", form_id)
        try:
            leads = get_leads_for_form(form_id)
        except requests.HTTPError as e:
            print(f"  ! Could not fetch leads for form '{form_name}': {e}")
            continue

        if force_resend:
            candidate_leads = leads
        else:
            candidate_leads = [l for l in leads if l["id"] not in processed]

        new_leads = []  # (lead, is_throttled) pairs
        for lead in candidate_leads:
            created_dt = parse_lead_created_time(lead.get("created_time", ""))
            in_live = live_since_dt is None or (created_dt is not None and created_dt >= live_since_dt)
            in_backfill = has_backfill and created_dt is not None and (
                (backfill_since_dt is None or created_dt >= backfill_since_dt)
                and (backfill_until_dt is None or created_dt <= backfill_until_dt)
            )
            if not in_live and not in_backfill:
                skipped_out_of_range += 1
                continue
            # Only leads that are ONLY in the backfill window (not also
            # "live") are subject to the per-run cap - today's real leads
            # are never held back.
            new_leads.append((lead, in_backfill and not in_live))

        if not new_leads:
            continue

        questions_lookup = get_form_questions(form_id)
        route = match_route(form_name, routing_rows)

        for lead, is_throttled in new_leads:
            if is_throttled and BACKFILL_MAX_PER_RUN and throttled_sent >= BACKFILL_MAX_PER_RUN:
                throttled_deferred += 1
                continue

            lead_id = lead["id"]
            name, email, phone, qa_pairs = extract_lead_fields(
                lead.get("field_data", []), questions_lookup
            )

            if route and route["recipient_email"]:
                html = build_email_html(
                    campaign_label=route["campaign_label"],
                    property_link=route["property_link"],
                    name=name,
                    email=email,
                    phone=phone,
                    qa_pairs=qa_pairs,
                )
                real_recipient = route["recipient_email"]
                to_email = TEST_RECIPIENT_EMAIL if TEST_MODE else real_recipient
                subject = f"Nuevo Lead: {route['campaign_label']} - {name}".strip()
                if TEST_MODE:
                    subject = f"[PRUEBA - iría a {real_recipient}] {subject}"
                bcc_email = None if TEST_MODE else AGENT_EMAIL_BCC
                # Log a human-readable label (agent name, falling back to the
                # campaign name), never the raw email address - this file's
                # Actions run logs are visible to anyone with repo access,
                # and the repo is public, so agent email addresses must never
                # be printed here.
                recipient_label = route["recipient_name"] or route["campaign_label"]
                try:
                    smtp.send(to_email, subject, html, bcc_email=bcc_email)
                    if TEST_MODE:
                        print(f"  -> [TEST MODE] Lead {lead_id} ({form_name}) would go to "
                              f"{recipient_label}; redirected to the test inbox instead")
                    else:
                        print(f"  -> Sent lead {lead_id} ({form_name}) to {recipient_label}")
                    processed.add(lead_id)
                    sent_count += 1
                    if is_throttled:
                        throttled_sent += 1
                    time.sleep(1)  # small, polite gap between sends
                except Exception as e:
                    print(f"  ! FAILED to send lead {lead_id} ({form_name}) to {recipient_label}: {e}")
                    # Do not mark as processed - we'll retry next run.
            else:
                if lead_id in alerted and not force_resend:
                    continue
                html = build_alert_html(
                    form_name, lead_id, lead.get("created_time", ""), name, email, phone, qa_pairs
                )
                try:
                    smtp.send(ALERT_EMAIL, f"⚠️ Lead sin campaña asignada: {form_name}", html)
                    print(f"  -> No route for form '{form_name}'; sent alert for lead {lead_id}")
                    alerted.add(lead_id)
                    alert_count += 1
                    if is_throttled:
                        throttled_sent += 1
                    time.sleep(1)  # small, polite gap - avoids tripping SMTP rate limits
                except Exception as e:
                    print(f"  ! FAILED to send unmatched-lead alert for {lead_id} "
                          f"({form_name}): {e}")

    smtp.close()

    state["processed"] = sorted(processed)
    state["alerted"] = sorted(alerted)
    save_state(state)

    summary = (f"Done. Sent {sent_count} lead email(s), {alert_count} alert(s), "
               f"skipped {skipped_out_of_range} lead(s) outside the date window.")
    if has_backfill:
        summary += (f" Backfill: {throttled_sent} sent this run, "
                    f"{throttled_deferred} held back for a later run.")
    print(summary)


if __name__ == "__main__":
    try:
        main()
    except Exception:
        import traceback
        traceback.print_exc()
        sys.exit(1)
