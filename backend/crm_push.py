"""Push high-fit, pre-start trials into the aicure-crm app as Leads.

The CRM (a separate app — see ~/aicure-crm) owns outreach: we hand it a Lead via
its public, shared-secret `POST /api/ingest/pipeline-lead` endpoint and it dedups,
optionally finds a missing email (Seamless.AI), and runs/tracks the cold email.
We only SELECT what to send and remember what we sent (crm_pushed_at) so a row is
never pushed twice.

Config (all via env; absent-safe — a no-op when unconfigured, like the emailer):
    CRM_PUSH_ENABLED    "1"/"true" to actually push. Default off.
    CRM_BASE_URL        e.g. https://crm.aicure.example  (no trailing /api)
    CRM_INGEST_TOKEN    shared secret == the CRM's PIPELINE_INGEST_TOKEN
    CRM_FIT_THRESHOLD   minimum aicure_fit to push (0-100). Default 70.
    CRM_PUSH_LIMIT      max rows per run (protects deliverability). Default 100.
    CRM_MIN_LEAD_DAYS   skip trials starting sooner than this — AiCure needs
                        runway to engage the sponsor before the protocol locks.
                        Default 182 (~6 months).
    CRM_MAX_LEAD_DAYS   optional upper bound — skip trials starting LATER than
                        this. Default 0 = no cap (any start at/after the floor
                        qualifies). Set a positive day count to add a ceiling;
                        set the floor to 0 as well to disable timing entirely.

Run standalone (`python3 crm_push.py`) or let ingest.py / reingest_news.py call
run() at the end of a pipeline pass.
"""

import os
import traceback
from datetime import datetime, timezone

from db import get_connection
from scoring import _days_from_now  # dateutil-based; handles YYYY-MM, DD/MM/YYYY, etc.

EXTERNAL_SOURCE = "Trial Pipeline"

# Sentinel so callers can pass an explicit max_days=None (cap disabled) and still
# be distinguished from "not supplied, read the env".
_ENV = object()

# Only pre-enrollment trials: the goal is to reach the sponsor BEFORE the trial
# starts (mirrors emailer.EARLY_STAGE_TYPES). NOT_YET_RECRUITING is the one true
# pre-start registry status; everything else is already underway or dead.
EARLY_STAGE_STATUSES = ("NOT_YET_RECRUITING",)


def _enabled():
    return bool(os.environ.get("CRM_BASE_URL")) and os.environ.get(
        "CRM_PUSH_ENABLED", ""
    ).strip().lower() in ("1", "true", "yes", "on")


def _threshold():
    raw = os.environ.get("CRM_FIT_THRESHOLD", "70")
    try:
        return int(raw)
    except ValueError:
        print(f"[crm_push] invalid CRM_FIT_THRESHOLD={raw!r} — using 70")
        return 70


def _limit():
    raw = os.environ.get("CRM_PUSH_LIMIT", "100")
    try:
        return int(raw)
    except ValueError:
        print(f"[crm_push] invalid CRM_PUSH_LIMIT={raw!r} — using 100")
        return 100


def _min_lead_days():
    """Minimum days from now until a trial's start for it to qualify for
    automated outreach. Trials starting sooner are skipped — AiCure needs runway
    to engage the sponsor before the protocol locks. Default ~6 months."""
    raw = os.environ.get("CRM_MIN_LEAD_DAYS", "182")
    try:
        return int(raw)
    except ValueError:
        print(f"[crm_push] invalid CRM_MIN_LEAD_DAYS={raw!r} — using 182")
        return 182


def _max_lead_days():
    """Optional upper bound on days from now until start. There is NO cap by
    default — any start at/after the floor qualifies. Set CRM_MAX_LEAD_DAYS to a
    positive day count to add a ceiling; 0/empty/invalid means uncapped.
    Returns None when uncapped."""
    raw = os.environ.get("CRM_MAX_LEAD_DAYS", "0").strip()
    if raw == "":
        return None
    try:
        v = int(raw)
    except ValueError:
        print(f"[crm_push] invalid CRM_MAX_LEAD_DAYS={raw!r} — leaving the cap off")
        return None
    return None if v <= 0 else v


def _within_lead_window(start_date, min_days=_ENV, max_days=_ENV):
    """True if start_date is far enough out (>= min_days) and not too far
    (<= max_days, when capped). A missing/unparseable date does NOT qualify:
    automated outreach must be able to confirm enough runway before the trial
    starts. With no floor and no cap the window is off — everything qualifies."""
    if min_days is _ENV:
        min_days = _min_lead_days()
    if max_days is _ENV:
        max_days = _max_lead_days()
    if min_days <= 0 and max_days is None:
        return True  # timing window disabled
    days = _days_from_now(start_date)
    if days is None:
        return False  # can't confirm timing → skip for automated outreach
    if days < min_days:
        return False
    if max_days is not None and days > max_days:
        return False
    return True


# Post-nominal credentials to drop ("Jane Powell, MD, PhD" -> "Jane Powell").
_CREDENTIALS = {
    "md", "phd", "ms", "msc", "mph", "rn", "do", "pharmd", "mbbs", "dr", "prof",
    "mba", "bsn", "np", "pa", "facc", "faha", "facp", "dvm", "dds", "frcp",
    "mrcp", "scd", "edd", "psyd", "msn", "msph", "bs", "ba", "jr", "sr",
}


def _split_name(full):
    """(first, last) from a PI/contact name. Strips credential segments, then
    handles both 'Last, First' and 'First M Last'. Greeting-friendly: first/last
    are single tokens. Returns (None, None) if empty."""
    if not full:
        return None, None
    segments = [s.strip() for s in str(full).split(",") if s.strip()]

    def is_cred(seg):
        words = seg.replace(".", "").lower().split()
        return bool(words) and all(w in _CREDENTIALS for w in words)

    # Credentials are post-nominal — strip them only from the END, never a leading
    # name segment. Short credential tokens collide with real surnames ("Ba",
    # "Do"), so dropping by membership anywhere would mangle "Ba, Mohamed".
    while segments and is_cred(segments[-1]):
        segments.pop()
    kept = segments
    if not kept:
        return None, None
    if len(kept) >= 2:
        # 'Last, First [Middle]' — surname segment first, given names second.
        first_tokens = kept[1].split()
        return (first_tokens[0] if first_tokens else None), (kept[0] or None)
    tokens = kept[0].split()  # 'First [Middle] Last'
    if len(tokens) == 1:
        return None, tokens[0]
    return tokens[0], tokens[-1]


def _org_contact(conn, trial_id):
    """Best contact (prefer a decision-maker with an email) linked to the trial's
    sponsor org, via trial_org_links → org_contacts. None if none on file."""
    row = conn.execute(
        """
        SELECT oc.full_name, oc.title, oc.email
        FROM trial_org_links tol
        JOIN org_contacts oc ON oc.org_id = tol.org_id
        WHERE tol.trial_id = ?
        ORDER BY (oc.email IS NOT NULL AND oc.email != '') DESC,
                 oc.is_decision_maker DESC
        LIMIT 1
        """,
        (trial_id,),
    ).fetchone()
    return row


def select_crm_candidates(conn, threshold=None, limit=None):
    """High-fit, pre-start trials we haven't pushed yet (best fit first) whose
    start date sits in the outreach lead-time window (see _within_lead_window):
    far enough out that AiCure can still engage the sponsor, not so far it's
    premature. Trials with no usable start date are skipped.

    The window can't be expressed in SQL — start_date holds mixed formats
    (YYYY-MM-DD, YYYY-MM, DD/MM/YYYY) that only dateutil parses — so we fetch the
    (inherently small) pre-start/high-fit/unpushed pool and filter in Python
    BEFORE applying the limit, so a near-term row can't crowd out a qualifying one."""
    threshold = _threshold() if threshold is None else threshold
    limit = _limit() if limit is None else limit
    placeholders = ",".join("?" for _ in EARLY_STAGE_STATUSES)
    rows = conn.execute(
        f"""
        SELECT * FROM trials
        WHERE aicure_fit >= ?
          AND status IN ({placeholders})
          AND sponsor IS NOT NULL AND sponsor != ''
          AND (crm_pushed_at IS NULL OR crm_pushed_at = '')
        ORDER BY aicure_fit DESC, id
        """,
        (threshold, *EARLY_STAGE_STATUSES),
    ).fetchall()
    min_days, max_days = _min_lead_days(), _max_lead_days()
    eligible = [r for r in rows if _within_lead_window(r["start_date"], min_days, max_days)]
    return eligible[:limit]


def build_payload(trial, conn):
    """Map a trial row → the CRM pipeline-lead payload. Prefer the PI as the
    contact; fall back to a sponsor-org contact; else a generic team name so the
    CRM's required lastName is satisfied (it can still enrich the email)."""
    first, last = _split_name(trial["pi_name"])
    email = (trial["pi_email"] or "").strip() or None
    title = "Principal Investigator" if last else None

    if not last or not email:
        oc = _org_contact(conn, trial["id"])
        if oc:
            if not last:
                first, last = _split_name(oc["full_name"])
                title = (oc["title"] or "").strip() or None
            if not email:
                email = (oc["email"] or "").strip() or None

    if not last:
        last = "Clinical Operations"
        first = None

    bits = []
    if trial["phase"]:
        bits.append(f"Phase {trial['phase']}")
    if trial["status"]:
        bits.append(trial["status"].replace("_", " ").title())
    if trial["aicure_fit"] is not None:
        bits.append(f"AiCure fit {trial['aicure_fit']}")
    desc = (trial["title_brief"] or trial["title_official"] or trial["id"]).strip()
    if trial["source_url"]:
        desc += f" — {trial['source_url']}"
    if bits:
        desc += "\n" + ", ".join(bits) + "."

    return {
        "externalSource": EXTERNAL_SOURCE,
        "externalId": trial["id"],
        "firstName": first,
        "lastName": last,
        "company": trial["sponsor"],
        "email": email,
        "title": title,
        "therapeuticFocus": trial["therapeutic_area"],
        "indicationFocus": trial["conditions"],
        "description": desc,
        "fitScore": trial["aicure_fit"],
    }


def push_lead(payload):
    """POST one lead to the CRM. Returns the parsed JSON response. Raises on a
    non-2xx so the caller can log+skip that row without aborting the batch."""
    import requests

    base = os.environ["CRM_BASE_URL"].rstrip("/")
    # Render's fromService injects a bare hostname; default to https when no scheme.
    if not base.startswith(("http://", "https://")):
        base = "https://" + base
    headers = {"Content-Type": "application/json"}
    token = os.environ.get("CRM_INGEST_TOKEN")
    if token:
        headers["X-Ingest-Token"] = token
    resp = requests.post(
        f"{base}/api/ingest/pipeline-lead", json=payload, headers=headers, timeout=20
    )
    if resp.status_code >= 300:
        raise RuntimeError(f"CRM responded {resp.status_code}: {resp.text[:300]}")
    return resp.json()


def mark_pushed(conn, trial_id, crm_lead_id, action=None):
    conn.execute(
        "UPDATE trials SET crm_lead_id = ?, crm_pushed_at = ?, crm_push_action = ? WHERE id = ?",
        (crm_lead_id, datetime.now(timezone.utc).isoformat(), action, trial_id),
    )
    conn.commit()


def run(conn=None):
    """Push all qualifying trials. No-op (returns 0) when unconfigured. Per-row
    failures are logged and skipped so one bad lead can't fail the run."""
    if not _enabled():
        print("[crm_push] disabled (set CRM_PUSH_ENABLED=1 and CRM_BASE_URL) — skipping.")
        return 0

    own_conn = conn is None
    conn = conn or get_connection()
    pushed = failed = 0
    try:
        candidates = select_crm_candidates(conn)
        cap = _max_lead_days()
        window = f"{_min_lead_days()}-{cap}d to start" if cap else f">= {_min_lead_days()}d to start"
        print(
            f"[crm_push] {len(candidates)} candidate trial(s) "
            f"(fit >= {_threshold()}, pre-start, {window}, not yet pushed)."
        )
        for t in candidates:
            try:
                result = push_lead(build_payload(t, conn))
                action = result.get("action")
                reason = result.get("reason")
                # Stamp even on suppressed/updated so we don't re-push next run —
                # but record WHY (action[:reason]) so a leadless stamp is explainable.
                stamped = f"{action}:{reason}" if reason else action
                if stamped:
                    stamped = stamped[:200]  # clamp unbounded upstream free text
                mark_pushed(conn, t["id"], result.get("leadId"), stamped)
                pushed += 1
                print(
                    f"  + {t['id']} -> lead {result.get('leadId')} "
                    f"({action}{f' — {reason}' if reason else ''})"
                )
            except Exception as e:  # noqa: BLE001 — keep the batch going
                failed += 1
                print(f"  ! {t['id']} push failed: {e}")
                # str(e) alone hides a systemic bug (e.g. a KeyError in
                # build_payload) vs. a one-off bad row — print the stack like
                # ingest.py does for its step failures.
                traceback.print_exc()
    finally:
        if own_conn:
            conn.close()

    print(f"[crm_push] done: {pushed} pushed, {failed} failed.")
    return 1 if failed and not pushed else 0


if __name__ == "__main__":
    import sys

    sys.exit(run())
