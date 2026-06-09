"""Daily/weekly market-intelligence email digests.

Delivery is pluggable via AICURE_EMAIL_BACKEND = resend | smtp | preview
(default: auto-detect — Resend if RESEND_API_KEY is set, else Gmail SMTP if
those creds are set, else a local preview file). No backend needed for the
pipeline to run end-to-end; it just writes previews until one is configured.

RESEND (recommended — free tier, no personal inbox, good deliverability):
    RESEND_API_KEY          API key from resend.com
    AICURE_EMAIL_FROM       From address. Default 'AiCure Digest <onboarding@resend.dev>'
                            (sandbox sender — only delivers to YOUR Resend account
                            email; verify a domain in Resend to send anywhere).

SMTP (Gmail or any host):
    AICURE_SMTP_USER        SMTP username (e.g. a Gmail address)
    AICURE_SMTP_PASSWORD    Gmail App Password (NOT the normal account password)
    AICURE_SMTP_HOST/PORT   Default smtp.gmail.com : 587

Common:
    AICURE_EMAIL_TO         Comma-separated recipients. REQUIRED for resend/smtp
                            delivery — sending raises if it is unset (there is no
                            default inbox). Empty is fine for the preview backend.
"""

import os
import re
import json
import smtplib
import html as _html
from email.mime.text import MIMEText
from email.mime.multipart import MIMEMultipart
from datetime import datetime, timedelta
from dateutil.parser import parse as dateparse

from db import get_connection
from scoring import (
    _illustrative_trial_score, _illustrative_grant_score,
    _trial_aicure_fit, _grant_aicure_fit, _fit_blurb,
)

PREVIEW_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "data", "email_previews")

# Display order + human labels for news event types. Strongest near-term
# commercial signal first; this ordering is also what the digest sorts by and
# is the seed of the 5-axis "immediacy" dimension (see notes in run_weekly /
# the scoring proposal).
# Ordered EARLIEST-stage first = highest value to AiCure. The goal is to reach
# a sponsor BEFORE the trial starts; by "first patient" / active recruitment the
# window to be the trial-tech partner has closed, so those are excluded from the
# daily opportunities email (see EARLY_STAGE_TYPES).
EVENT_TYPE_DISPLAY = [
    ("protocol_planning",      "Protocol Planning (earliest)"),
    ("funding_awarded",        "Funding Awarded"),
    ("vendor_signal",          "Vendor / Outsourcing RFP"),
    ("registry_change",        "Newly Registered / Filing"),
    ("study_startup",          "Study Startup (pre-enrollment)"),
    ("site_opening",           "Site Activation"),
    ("recruitment_initiation", "Already Recruiting (likely too late)"),
    ("trial_results",          "Trial Results (too late)"),
]

# Event types worth emailing as opportunities — strictly pre-enrollment.
# Recruitment/results are deliberately omitted: if AiCure reaches out then, it
# is already too late to win the trial.
EARLY_STAGE_TYPES = [
    "protocol_planning", "funding_awarded", "vendor_signal",
    "registry_change", "study_startup", "site_opening",
]


# ── delivery ────────────────────────────────────────────────────────────────

def _recipients():
    raw = os.environ.get("AICURE_EMAIL_TO", "")
    return [r.strip() for r in raw.split(",") if r.strip()]


def _backend():
    """Delivery backend: AICURE_EMAIL_BACKEND = resend | smtp | preview.
    Default auto: Resend if RESEND_API_KEY set, else SMTP if Gmail creds set,
    else write a local preview file."""
    b = os.environ.get("AICURE_EMAIL_BACKEND")
    if b:
        return b
    if os.environ.get("RESEND_API_KEY"):
        return "resend"
    if os.environ.get("AICURE_SMTP_USER") and os.environ.get("AICURE_SMTP_PASSWORD"):
        return "smtp"
    return "preview"


def send_email(subject, html_body):
    """Deliver a digest via the configured backend. Returns a status string."""
    recipients = _recipients()
    backend = _backend()
    if backend in ("resend", "smtp") and not recipients:
        # Fail loudly rather than silently dropping a curated lead digest into
        # the void (or, historically, a hardcoded personal inbox).
        raise RuntimeError(
            "AICURE_EMAIL_TO is unset — refusing to send a digest with no "
            "recipients. Set AICURE_EMAIL_TO (comma-separated) before sending."
        )
    if backend == "resend":
        return _send_resend(subject, html_body, recipients)
    if backend == "smtp":
        return _send_smtp(subject, html_body, recipients)
    return _send_preview(subject, html_body, recipients)


def _send_resend(subject, html_body, recipients):
    """Send via the Resend HTTP API (free tier). Needs RESEND_API_KEY.
    `from` must be a Resend-verified domain, or `onboarding@resend.dev` for the
    sandbox (which only delivers to your own Resend account email)."""
    import requests
    sender = os.environ.get("AICURE_EMAIL_FROM", "AiCure Digest <onboarding@resend.dev>")
    resp = requests.post(
        "https://api.resend.com/emails",
        headers={"Authorization": f"Bearer {os.environ['RESEND_API_KEY']}",
                 "Content-Type": "application/json"},
        json={"from": sender, "to": recipients, "subject": subject, "html": html_body},
        timeout=30,
    )
    if resp.status_code >= 300:
        raise RuntimeError(f"Resend error {resp.status_code}: {resp.text[:300]}")
    rid = (resp.json() or {}).get("id", "?")
    status = f"[emailer] Resend sent '{subject}' to {', '.join(recipients)} (id={rid})"
    print(status)
    return status


def _send_smtp(subject, html_body, recipients):
    """Send via Gmail (or any) SMTP with STARTTLS. Needs AICURE_SMTP_USER/PASSWORD."""
    user = os.environ["AICURE_SMTP_USER"]
    sender = os.environ.get("AICURE_EMAIL_FROM", user)
    host = os.environ.get("AICURE_SMTP_HOST", "smtp.gmail.com")
    port = int(os.environ.get("AICURE_SMTP_PORT", "587"))

    msg = MIMEMultipart("alternative")
    msg["Subject"] = subject
    msg["From"] = sender
    msg["To"] = ", ".join(recipients)
    msg.attach(MIMEText(html_body, "html"))

    with smtplib.SMTP(host, port) as server:
        server.starttls()
        server.login(user, os.environ["AICURE_SMTP_PASSWORD"])
        server.sendmail(sender, recipients, msg.as_string())
    status = f"[emailer] SMTP sent '{subject}' to {', '.join(recipients)}"
    print(status)
    return status


def _send_preview(subject, html_body, recipients):
    """No delivery backend configured — write the rendered email to a file."""
    os.makedirs(PREVIEW_DIR, exist_ok=True)
    stamp = datetime.utcnow().strftime("%Y%m%d_%H%M%S")
    path = os.path.join(PREVIEW_DIR, f"digest_{stamp}.html")
    to_label = ", ".join(recipients) if recipients else "(none configured)"
    with open(path, "w", encoding="utf-8") as f:
        f.write(f"<!-- TO: {to_label} | SUBJECT: {subject} -->\n")
        f.write(html_body)
    msg = f"[emailer] No backend configured — preview written to {path}"
    print(msg)
    return msg


# ── rendering helpers ─────────────────────────────────────────────────────────

def _esc(v):
    return _html.escape(str(v)) if v is not None else ""


def _shell(title, intro, sections):
    """Wrap content sections in a simple, email-client-safe HTML shell."""
    body = [
        '<div style="font-family:-apple-system,Segoe UI,Roboto,Arial,sans-serif;'
        'max-width:720px;margin:0 auto;color:#1a1a1a;">',
        f'<h1 style="font-size:20px;margin:0 0 4px;">{_esc(title)}</h1>',
        f'<p style="color:#666;margin:0 0 20px;font-size:13px;">{_esc(intro)}</p>',
    ]
    if not sections:
        body.append('<p style="color:#999;">No new items in this window.</p>')
    else:
        body.extend(sections)
    body.append(
        '<hr style="border:none;border-top:1px solid #eee;margin:24px 0 8px;">'
        '<p style="color:#aaa;font-size:11px;">AiCure market-intelligence digest — '
        'internal/testing only. Reply-list is configurable via AICURE_EMAIL_TO.</p>'
        '</div>'
    )
    return "\n".join(body)


NA = '<span style="color:#bbb;">—</span>'  # shown when a field couldn't be found


def _val(v):
    return _esc(v) if (v not in (None, "", 0)) else NA


_EMAIL_RE = re.compile(r'^[^@\s]+@[^@\s]+\.[^@\s]+$')


def _cell(v):
    """Render a field value; turn an email address into a clickable mailto link."""
    if isinstance(v, str) and _EMAIL_RE.match(v.strip()):
        e = _esc(v.strip())
        return f'<a href="mailto:{e}" style="color:#1a4fa0;text-decoration:none;">{e}</a>'
    return _val(v)


def _lead_card(title, url, score, score_why, abstract, fields, tag=None, blurb=None):
    """Shared lead-card used by all three digests.

    fields: list of (label, value) pairs — value None/"" renders as "—".
    score:  0-100 illustrative opportunity score (None hides the score chip).
    blurb:  prominent "why this works for AiCure" sentence (product-level).
    """
    tag_html = (
        f'<span style="background:#eef;color:#335;border-radius:3px;'
        f'padding:1px 6px;font-size:11px;margin-right:6px;">{_esc(tag)}</span>'
        if tag else ""
    )
    title_html = (
        f'<a href="{_esc(url)}" style="color:#1a4fa0;text-decoration:none;">{_esc(title)}</a>'
        if url else _esc(title)
    )
    score_html = ""
    if score is not None:
        score_html = (
            f'<div style="float:right;text-align:right;">'
            f'<span style="background:#1a4fa0;color:#fff;border-radius:10px;padding:2px 9px;'
            f'font-size:12px;font-weight:700;">{int(round(score))}</span>'
            f'<div style="color:#999;font-size:10px;margin-top:2px;max-width:170px;">{_esc(score_why)}</div>'
            f'</div>'
        )
    abstract_html = (
        f'<div style="color:#444;font-size:12px;margin:6px 0;line-height:1.4;">{_esc(abstract)}</div>'
        if abstract else ""
    )
    # "Why AiCure" is rendered as a normal field row (same format as the rest),
    # placed first, rather than a separate callout box.
    all_fields = ([("Why AiCure", blurb)] if blurb else []) + list(fields)
    field_rows = "".join(
        f'<tr><td style="color:#888;font-size:11px;padding:1px 10px 1px 0;white-space:nowrap;'
        f'vertical-align:top;">{_esc(label)}</td>'
        f'<td style="font-size:12px;padding:1px 0;">{_cell(value)}</td></tr>'
        for label, value in all_fields
    )
    return (
        '<div style="border:1px solid #eee;border-radius:6px;padding:11px 13px;margin:0 0 11px;">'
        f'{score_html}'
        f'<div style="font-size:14px;font-weight:600;margin-bottom:2px;">{tag_html}{title_html}</div>'
        f'{abstract_html}'
        f'<table style="border-collapse:collapse;margin-top:4px;">{field_rows}</table>'
        '<div style="clear:both;"></div></div>'
    )


def _money(v):
    return f"${v:,.0f}" if v else None


def _titlecase(v):
    """Title-case ALL-CAPS names (grant feeds shout: 'THOMAS JEFFERSON UNIVERSITY')
    while leaving normal/mixed-case and short acronyms alone."""
    if not v:
        return v
    s = str(v)
    letters = [c for c in s if c.isalpha()]
    if letters and sum(c.isupper() for c in letters) / len(letters) > 0.7 and len(s) > 4:
        return s.title()
    return s


def _jlist(v):
    try:
        x = json.loads(v) if v else []
        return x if isinstance(x, list) else [x]
    except Exception:
        return [v] if v else []


def _clean_excerpt(text, n=200):
    """Strip boilerplate ('PROJECT SUMMARY', 'Abstract:'), collapse whitespace,
    and cut at a sentence boundary instead of mid-word."""
    if not text:
        return ""
    t = text.strip().lstrip("/\\.-–:* \t")
    t = re.sub(r'(?i)^\s*(project\s+summary|abstract|description|summary|background|narrative)'
               r'\s*[:\-–/]*\s*', "", t)
    t = " ".join(t.split())
    if len(t) <= n:
        return t
    cut = t[:n]
    for sep in (". ", "? ", "! "):
        i = cut.rfind(sep)
        if i > n * 0.5:
            return cut[:i + 1]
    i = cut.rfind(" ")
    return (cut[:i] if i > 0 else cut) + "…"


_PHASE_LABEL = {"phase1": "Phase 1", "phase2": "Phase 2", "phase3": "Phase 3",
                "phase4": "Phase 4", "early_phase1": "Early Phase 1"}


def _trial_summary(t):
    """A readable, comprehensive project summary from trial fields — everything
    important in prose; the field table below is the categorized breakdown."""
    conds = _jlist(t["conditions"])
    intervs = _jlist(t["interventions"])
    plab = _PHASE_LABEL.get((t["phase"] or "").lower().replace(" ", ""))
    head = f"{plab} trial" if plab else "Study"
    cond = conds[0] if conds else (t["therapeutic_area"] or "the indication")
    parts = [head]
    if intervs:
        parts.append(f"of {intervs[0]}")
    parts.append(f"in {cond}")
    if t["sponsor"]:
        parts.append(f"by {t['sponsor']}")
    summ = " ".join(parts) + "."

    scale = []
    if t["enrollment"]:
        scale.append(f"{t['enrollment']:,} participants")
    if t["num_sites"]:
        scale.append(f"{t['num_sites']} sites")
    if t["lead_country"]:
        scale.append(t["lead_country"])
    if scale:
        summ += " " + ", ".join(scale).capitalize() + "."

    stage = (t["status"] or "").replace("_", " ").lower()
    if stage:
        s2 = stage
        if t["start_date"]:
            s2 += f", starts {str(t['start_date'])[:10]}"
        summ += " " + s2.capitalize() + "."
    return summ


def _grant_summary(g):
    """Comprehensive grant summary — structured the same way as _trial_summary:
    what+who → scale → timing → a descriptive excerpt (the grant's 'what')."""
    # 1. WHAT + WHO  (parallel to trial's phase/intervention/condition/sponsor)
    area = g["therapeutic_area"] if g["therapeutic_area"] not in (None, "", "Other") else "Research"
    parts = [f"{area} project"]
    if g["organization"]:
        parts.append(f"at {_titlecase(g['organization'])}")
    if g["sponsor_funder"]:
        parts.append(f"funded by {_titlecase(g['sponsor_funder'])}")
    summ = " ".join(parts) + "."

    # 2. SCALE  (parallel to trial's participants/sites/country)
    scale = []
    if g["amount_usd"]:
        scale.append(_money(g["amount_usd"]))
    if g["country"]:
        scale.append(g["country"])
    if scale:
        summ += " " + ", ".join(scale) + "."

    # 3. TIMING  (parallel to trial's status/start date)
    timing = []
    if g["award_date"]:
        timing.append(f"awarded {str(g['award_date'])[:10]}")
    if g["start_date"] or g["end_date"]:
        rng = "–".join(filter(None, [str(g["start_date"])[:10] if g["start_date"] else "",
                                     str(g["end_date"])[:10] if g["end_date"] else ""]))
        timing.append(f"runs {rng}")
    if timing:
        summ += " " + ", ".join(timing).capitalize() + "."

    # 4. DESCRIPTIVE EXCERPT  (the grant's 'what', like a trial's intervention)
    exc = _clean_excerpt(g["abstract"])
    return (summ + " " + exc) if exc else summ


# AiCure fit scoring now lives in scoring.py (shared by the API and tests).

# ── digest builders ────────────────────────────────────────────────────────────

def build_news_digest(hours=24, max_items=10, fetch=False, pick_titles=None):
    """Pre-start, AiCure-relevant news leads.

    Pipeline: keyword sorter (event_type) → NLP relevance + extraction
    (news_nlp.analyze) → keep only items that (a) apply to AiCure's focus and
    (b) are not yet started → rank earliest-stage first → top `max_items`.
    Cards show only fields the NLP can actually fill from the article.

    pick_titles: optional list of title substrings to hand-select specific
    verified items (ignores the time window) — used to assemble a POC digest.
    """
    import news_nlp
    cutoff = (datetime.utcnow() - timedelta(hours=hours)).isoformat()
    order = {et: i for i, et in enumerate(EARLY_STAGE_TYPES)}
    placeholders = ",".join("?" for _ in EARLY_STAGE_TYPES)
    conn = get_connection()
    if pick_titles:
        like = " OR ".join("title LIKE ?" for _ in pick_titles)
        rows = conn.execute(
            f"SELECT source, title, url, published_at, body_snippet, drug_mentioned, "
            f"sponsor_mentioned, phase_mentioned, nct_ids_found, event_type "
            f"FROM news_items WHERE {like}",
            tuple(f"%{t}%" for t in pick_titles),
        ).fetchall()
    else:
        rows = conn.execute(
            f"""
            SELECT source, title, url, published_at, body_snippet, drug_mentioned,
                   sponsor_mentioned, phase_mentioned, nct_ids_found, event_type
            FROM news_items
            WHERE ingested_at >= ? AND event_type IN ({placeholders})
            ORDER BY ingested_at DESC
            """,
            (cutoff, *EARLY_STAGE_TYPES),
        ).fetchall()
    conn.close()

    label_map = dict(EVENT_TYPE_DISPLAY)
    candidates = []
    considered = 0
    for r in sorted(rows, key=lambda r: order.get(r["event_type"], 99)):
        considered += 1
        item = dict(r)
        ft = news_nlp.fetch_article_text(r["url"]) if fetch else None
        a = news_nlp.analyze(item, full_text=ft)
        # NLP gate: must be AiCure-relevant AND not yet started.
        if not (a["applies_to_aicure"] and a["not_yet_started"]):
            continue
        candidates.append((r, a))
        if len(candidates) >= max_items:
            break

    sections = []
    for r, a in candidates:
        # Only render fields that actually have a value (plus always-present
        # stage/category/source) — keeps cards full instead of "—" spam.
        fields = [("Signal / stage", label_map.get(r["event_type"], r["event_type"])),
                  ("AiCure category", a["aicure_category"])]
        for label, val in [
            ("Why flagged", a.get("signal_phrase")),
            ("Drug", r["drug_mentioned"]),
            ("Sponsor / Org", a.get("sponsor_org")),
            ("Est. size", a.get("est_size")),
            ("Geography", a.get("geography")),
            ("Phase", r["phase_mentioned"]),
        ]:
            if val:
                fields.append((label, val))
        fields.append(("Source", f'{r["source"]} · {r["published_at"]}'))
        nblurb = _fit_blurb(a.get("fit_signals") or [])
        sections.append(_lead_card(
            r["title"], r["url"], score=None, score_why="",
            abstract=_clean_excerpt(r["body_snippet"], 220),
            fields=fields, tag=a["aicure_category"], blurb=nblurb,
        ))

    intro = (f"{len(candidates)} AiCure-relevant PRE-START signals (of {considered} "
             f"early-stage items screened). NLP keeps only cardiometabolic/adherence "
             f"trials that have not yet started; recruiting/results and off-focus "
             f"(e.g. oncology) items are dropped.")
    return _shell("AiCure — Daily News Signals", intro, sections), len(candidates)


def build_weekly_trials_digest(days=7, top_n=10):
    """Top-N newly registered trials by opportunity score.

    Windows on first_posted (the registry's own "first posted" date, ~96%
    filled) rather than ingested_at. The pullers use INSERT OR REPLACE and
    re-stamp ingested_at on every pull, so it marks "last pulled", not "newly
    registered" — first_posted is the authoritative registration date.
    """
    cutoff = (datetime.utcnow() - timedelta(days=days)).strftime("%Y-%m-%d")
    conn = get_connection()
    trials = conn.execute(
        """
        SELECT id, title_brief, brief_summary, source_url, sponsor, phase, status,
               therapeutic_area, lead_country, start_date, enrollment, num_sites,
               pi_name, pi_email, registry_sources, interventions, conditions,
               epro_ecoa, digital_biomarkers, dct_elements
        FROM trials
        WHERE first_posted >= ?
          AND status NOT IN ('COMPLETED','TERMINATED','SUSPENDED','WITHDRAWN',
                             'NO_LONGER_AVAILABLE','APPROVED_FOR_MARKETING')
        ORDER BY first_posted DESC
        LIMIT 3000
        """,
        (cutoff,),
    ).fetchall()
    conn.close()

    scored = sorted(((*_illustrative_trial_score(t), t) for t in trials),
                    key=lambda x: x[0], reverse=True)[:top_n]

    sections = []
    for score, why, t in scored:
        try:
            regs = ", ".join(json.loads(t["registry_sources"] or "[]"))
        except Exception:
            regs = t["registry_sources"] or ""
        _fp, fwhy, _has = _trial_aicure_fit(t)
        blurb = _fit_blurb(fwhy)
        # Only fields NOT already stated in the prose summary (which covers
        # phase, sponsor, enrollment, sites, country, status, start date).
        fields = [
            ("Therapeutic area", t["therapeutic_area"]),
            ("PI", t["pi_name"]),
            ("Email", t["pi_email"]),
            ("Registry", regs or None),
        ]
        sections.append(_lead_card(
            t["title_brief"] or t["id"], t["source_url"], score, why,
            abstract=_trial_summary(t), fields=fields, blurb=blurb,
        ))

    intro = f"Top {len(scored)} of newly registered trials this week, ranked by opportunity score (pre-start favored)."
    return _shell("AiCure — Weekly Registered Trials", intro, sections), len(scored)


def build_weekly_grants_digest(days=7, top_n=10):
    """Top-N new grants by opportunity score (same scorer, source-aware).

    Windows on first_seen (set once, preserved across re-pulls — see
    grant_utils.upsert_grant) rather than the re-stamped ingested_at. Source
    award/start dates are too sparse to filter on (award_date ~20% filled), so
    "new" = first time we saw the grant. Falls back to ingested_at if a row
    predates the first_seen backfill.
    """
    cutoff = (datetime.utcnow() - timedelta(days=days)).isoformat()
    # Exclude grants that already ended >1yr ago — same rule prune_old uses to
    # delete them. A re-ingest re-fetches lots of long-finished awards and
    # stamps first_seen=now, which would otherwise flood the digest with stale
    # "new" grants. Grants with no end_date are kept (can't be shown as old).
    end_cutoff = (datetime.utcnow() - timedelta(days=365)).strftime("%Y-%m-%d")
    conn = get_connection()
    grants = conn.execute(
        """
        SELECT id, title, abstract, source_url, organization, sponsor_funder,
               amount_usd, therapeutic_area, conditions, phase_mentioned,
               country, award_date, start_date, end_date, linked_trial_id,
               pi_name, pi_email, source
        FROM grants
        WHERE COALESCE(NULLIF(first_seen, ''), ingested_at) >= ?
          AND (end_date IS NULL OR end_date = '' OR end_date >= ?)
        LIMIT 20000
        """,
        (cutoff, end_cutoff),
    ).fetchall()
    conn.close()

    scored = sorted(((*_illustrative_grant_score(g), g) for g in grants),
                    key=lambda x: x[0], reverse=True)[:top_n]

    sections = []
    for score, why, g in scored:
        linked = "yes — see registry" if g["linked_trial_id"] else None
        _fp, gwhy, _has = _grant_aicure_fit(g)
        gblurb = _fit_blurb(gwhy)
        # Only fields NOT already in the prose summary (which now covers org,
        # amount, funder, area, country, award date, and project run dates).
        # Grants carry no PI email in any feed (0% coverage) — omit the row.
        fields = [
            ("Linked trial", linked),
            ("PI", _titlecase(g["pi_name"])),
            ("Source", g["source"]),
        ]
        sections.append(_lead_card(
            g["title"] or g["id"], g["source_url"], score, why,
            abstract=_grant_summary(g), fields=fields, blurb=gblurb,
        ))

    intro = f"Top {len(scored)} new grants this week, ranked by opportunity score (early-stage favored)."
    return _shell("AiCure — Weekly Grants", intro, sections), len(scored)


# ── scheduled entry points ─────────────────────────────────────────────────────

def send_daily_news_digest():
    """Daily, but suppress empty days. The relevant signal is rare yet
    time-critical (AiCure must engage pre-start), so we keep the fast daily
    cadence and simply don't send on days with fewer than the threshold of
    qualifying items — avoiding the empty-inbox fatigue that would push us to a
    slower weekly cadence. Set AICURE_NEWS_MIN_ITEMS=0 to always send."""
    min_items = int(os.environ.get("AICURE_NEWS_MIN_ITEMS", "1"))
    html_body, n = build_news_digest(hours=24)
    if n < min_items:
        msg = f"[emailer] Daily news skipped — {n} qualifying item(s) (< {min_items})"
        print(msg)
        return msg
    subject = f"AiCure Daily News — {n} early-stage signal{'s' if n != 1 else ''} ({datetime.utcnow():%Y-%m-%d})"
    return send_email(subject, html_body)


def send_weekly_trials_digest():
    html_body, n = build_weekly_trials_digest(days=7)
    subject = f"AiCure Weekly Trials — top {n} ({datetime.utcnow():%Y-%m-%d})"
    return send_email(subject, html_body)


def send_weekly_grants_digest():
    html_body, n = build_weekly_grants_digest(days=7)
    subject = f"AiCure Weekly Grants — top {n} ({datetime.utcnow():%Y-%m-%d})"
    return send_email(subject, html_body)


if __name__ == "__main__":
    # Manual run: `python emailer.py [news|trials|grants]`
    import sys
    which = sys.argv[1] if len(sys.argv) > 1 else "news"
    if which == "trials":
        print(send_weekly_trials_digest())
    elif which == "grants":
        print(send_weekly_grants_digest())
    else:
        print(send_daily_news_digest())
