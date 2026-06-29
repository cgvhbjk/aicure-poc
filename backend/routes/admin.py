"""Admin routes — split out of api.py.

Shared helpers/models/constants stay in api.py; we copy its module globals so
the moved handler bodies resolve bare names (row_to_dict, get_connection,
_trials_where, OrgUpdate, …) exactly as before — no fragile per-name import list.
"""
from fastapi import APIRouter
import api as _api

globals().update({k: v for k, v in vars(_api).items() if not k.startswith('__')})

router = APIRouter()


@router.post("/admin/refresh-news")
def admin_refresh_news(x_admin_key: str = Header(default="")):
    """Manually trigger a news refresh + cleanup. Protected by X-Admin-Key header."""
    _require_admin(x_admin_key)
    # Hold the lock for the whole job, not just this check: releasing it here made
    # the 409 "already in progress" guard decorative (a second call would acquire
    # the just-freed lock and start a concurrent refresh). The worker releases it
    # in finally.
    if not _news_refresh_lock.acquire(blocking=False):
        raise HTTPException(status_code=409, detail="Refresh already in progress")

    def _refresh_job():
        # run_daily_news re-raises on refresh failure ON PURPOSE; routing it
        # through a daemon thread would otherwise re-hide that. The endpoint
        # already returned {"started"}, so log the traceback or the operator who
        # triggered the manual refresh never learns it failed.
        try:
            run_daily_news()
        except Exception:
            print("[admin] background news refresh FAILED:")
            traceback.print_exc()
        finally:
            _news_refresh_lock.release()

    try:
        thread = threading.Thread(target=_refresh_job, daemon=True)
        thread.start()
    except Exception:
        _news_refresh_lock.release()  # never leak the lock if the thread won't start
        raise
    return {"status": "started", "message": "News refresh running in background"}


@router.post("/admin/send-news-digest")
def admin_send_news_digest(refresh: bool = True, x_admin_key: str = Header(default="")):
    """Refresh news (unless refresh=false) then build + SEND the daily news
    digest. Protected by X-Admin-Key. Meant to be hit by an external daily cron
    so delivery works even while the free-tier Render service is asleep (the
    request wakes it). Runs synchronously and returns the emailer result so the
    caller gets a real status (sent / skipped-empty / error)."""
    _require_admin(x_admin_key)
    try:
        detail = run_daily_news_and_send(refresh=refresh)
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"digest send failed: {e}")
    return {"status": "ok", "detail": detail}


@router.post("/admin/send-weekly-digest")
def admin_send_weekly_digest(x_admin_key: str = Header(default="")):
    """Build + SEND the weekly trials and grants digests (two emails) from the
    CURRENT DB. Read-only keyword scoring, no ingest — so it's light enough to
    run on free-tier Render. Driven by the weekly GitHub Actions cron. Note:
    content freshness is bounded by how recently the DB was ingested/deployed;
    this endpoint does not scrape (see the ingest discussion in the PR)."""
    _require_admin(x_admin_key)
    import emailer
    try:
        trials = emailer.send_weekly_trials_digest()
        grants = emailer.send_weekly_grants_digest()
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"weekly digest send failed: {e}")
    return {"status": "ok", "trials": trials, "grants": grants}


@router.post("/admin/prune-old")
def admin_prune_old(
    background_tasks: BackgroundTasks,
    dry_run: bool = True,
    cutoff_days: int = 365,
    x_admin_key: str = Header(default=""),
):
    """Remove trials/grants with primary_completion/end_date older than cutoff_days.

    Defaults to dry_run=True; pass dry_run=false to actually delete.
    """
    _require_admin(x_admin_key)
    from prune_old import prune_old
    if dry_run:
        trial_count, grant_count = prune_old(dry_run=True, cutoff_days=cutoff_days)
        return {"trials_pruned": trial_count, "grants_pruned": grant_count, "dry_run": True}
    def _prune_job():
        # Destructive delete in the background: the caller already got
        # {"started"}, so log the outcome (and any failure's traceback) — there's
        # otherwise no durable record that the prune ran, partially ran, or died.
        try:
            t, g = prune_old(dry_run=False, cutoff_days=cutoff_days)
            print(f"[admin] background prune done: {t} trials, {g} grants removed (cutoff_days={cutoff_days})")
        except Exception:
            print("[admin] background prune FAILED:")
            traceback.print_exc()

    background_tasks.add_task(_prune_job)
    return {"status": "started", "message": "Prune running in background", "dry_run": False}
