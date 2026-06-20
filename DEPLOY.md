# AiCure POC — Deployment runbook (Render → AWS ECS Fargate + EFS)

**Status: 2026-06-20.** Now part of the **monorepo** (`github.com/cgvhbjk/aicure`,
this app under `poc/`). App code (Part A) + security hardening are **done**;
**pick up at Part B (AWS infra)**. Build the image with context `poc/`
(`docker build -f poc/Dockerfile poc`). The multi-app deploy (CRM + POC on one
domain) is the monorepo-root `render.yaml`; this runbook is the POC's standalone
AWS path.

## Why this migration
Render free tier has no persistent disk, so the 331 MB SQLite DB is rebuilt from
scratch by `ingest.py` on every deploy (~5 min). The goal is the opposite: a DB
that updates **incrementally** — the daily scheduler and admin endpoints append a
few records to a **durable** DB that survives redeploys. ECS Fargate + EFS gives
that (the DB lives on an EFS volume). Chosen over App Runner (ephemeral disk) and
RDS (SQLite can't multi-write).

## What's already done (committed; now in the monorepo under `poc/`)
**App code (Part A)**
- `db.py` honors `AICURE_DB_PATH` + `AICURE_DB_NETWORK_FS=1` → `journal_mode=DELETE`,
  `mmap_size=0` (WAL and mmap are **unsafe over EFS/NFS** even when the PRAGMA reports success).
- `/healthz` liveness+data check (503 on an unopenable/empty DB — catches an LFS-pointer boot).
- `.dockerignore` keeps `.env` and the 7.9 GB snapshots out of the image.
- `entrypoint.sh` seeds the EFS DB from the baked-in copy on first (empty) boot,
  **before** Python imports `db`, atomically (`cp` to `.tmp` then `mv`). `PORT/EXPOSE 8080`.
- Dockerfile runs as **non-root uid 10001**.

**Security / correctness (deep-review P1/P2)**
- App-wide HTTP Basic auth gate (`AICURE_APP_PASSWORD`); valid `X-Admin-Key` bypasses it;
  `/healthz` + `OPTIONS` exempt; loud startup warning when left open.
- Per-request connection-leak guard; trial-merge `grant_trial_links` reassignment + undo;
  uploads persist to the EFS DB dir; constant-time admin-key compare; CORS-open startup warning.
- Tests: the `poc/backend` pytest suite (scoring, query builders, merge, CRM push) — **62 pass**.

## Next steps — do these in order

### 0. Local verify (code is already in the monorepo on `main`)
```
cd poc
pip install -r backend/requirements-dev.txt
python -m pytest backend -q
```

Then build + run the image (only if Docker Desktop is running). Set a real
password; `aic_efs` is a throwaway local volume standing in for EFS:
```
docker build --platform linux/amd64 -t aicure:test .
docker run --rm -e AICURE_DB_PATH=/data/aicure.db -e AICURE_DB_NETWORK_FS=1 -e AICURE_APP_PASSWORD=test -e PORT=8080 -v aic_efs:/data -p 8080:8080 aicure:test
```
Checks: `GET /healthz` returns 200; `docker restart` → `/data` persists and does
**not** re-seed; and the image carries no secrets:
```
docker run --rm aicure:test sh -c "ls /app/backend/.env || echo ABSENT"
```

### Part B — AWS infra
- [ ] **ECR**: repo `aicure-poc`; after `git lfs pull`, build `--platform linux/amd64`; push.
- [ ] **EFS**: filesystem (Elastic throughput, encrypted, **automatic backups ON**);
      **access point with POSIX uid/gid 10001 and root-dir creation owner 10001:10001**
      (must match the container's non-root uid, or the first-boot seed copy to `/data`
      is denied); mount targets in the task subnets; SG allowing TCP **2049** from the task SG.
- [ ] **Task def** `aicure-poc` (Fargate, **1 vCPU / 2 GB**, awslogs), container port 8080,
      EFS **volume mounted at `/data`** (access point + transit encryption); container
      healthCheck `curl -f http://localhost:8080/healthz`, **startPeriod 180s** (first-boot
      seed copy). Secrets + env from the table below.
- [ ] **Service**: `desiredCount=1`, **minimumHealthyPercent=0 / maximumHealthyPercent=100**
      (stop-then-start — single writer; never run >1 task).
- [ ] **ALB**: internet-facing; target group (ip type, port 8080, health `/healthz`);
      HTTPS:443 (ACM cert for the subdomain) + HTTP:80→443 redirect; SGs wired ALB↔task on 8080.
- [ ] **DNS**: ALIAS/CNAME the chosen subdomain → ALB. Resend DNS untouched.
- [ ] **IAM**: execution role (ECR pull, SSM read, CW logs); task role (EFS access-point auth).

### Part C — Cutover
- [ ] Smoke-test the **ALB DNS** (see Verification) before flipping anything.
- [ ] Point the subdomain DNS at the ALB; verify HTTPS.
- [ ] Update the repo **variable `RENDER_SERVICE_URL`** → new URL (keep the name — zero
      workflow edits), then manually dispatch `daily-news.yml` and confirm 200 + email.
- [ ] Watch one real scheduled run (06:00 UTC).
- [ ] **Decommission Render** (delete the service). `weekly-digest.yml` (runs in-runner)
      and `tests.yml` need no changes.

## Task-def secrets + env
| Kind | Key | Value / note |
|---|---|---|
| secret | `RESEND_API_KEY` | send key from the **helfandother@gmail.com** Resend account (domain benjaminhelfand.com is verified there) |
| secret | `ADMIN_KEY` | must match the `ADMIN_KEY` GitHub secret (the daily-news cron uses it) |
| secret | `AICURE_APP_PASSWORD` | **REQUIRED** — unset → the app ships **OPEN** (the login gate is a no-op) |
| env | `AICURE_DB_PATH` | `/data/aicure.db` |
| env | `AICURE_DB_NETWORK_FS` | `1` |
| env | `PORT` | `8080` |
| env | `AICURE_CORS_ORIGINS` | the real origin, e.g. `https://<subdomain>` (unset → startup warns, allows `*`) |
| env | `AICURE_EMAIL_FROM` | `AiCure Digest <digest@benjaminhelfand.com>` |
| env | `AICURE_EMAIL_TO` | your recipient (currently benjaminhelfand@gmail.com) |
| env (optional) | `AICURE_APP_USER` | Basic-auth username (default `aicure`) |
| env (optional) | `ANTHROPIC_API_KEY` | enables LLM news classification |
| env (optional) | `CRM_PUSH_ENABLED` | `1` to push high-fit, pre-start trials to the CRM as leads. Unset → `crm_push.run()` is a no-op. |
| env (optional) | `CRM_BASE_URL` | the deployed aicure-crm origin, e.g. `https://crm.aicure.example` (no trailing `/api`). Required for the push. |
| secret (optional) | `CRM_INGEST_TOKEN` | shared secret == the CRM's `PIPELINE_INGEST_TOKEN`. Sent as `X-Ingest-Token`. |
| env (optional) | `CRM_FIT_THRESHOLD` | min `aicure_fit` to push (default `70`). |
| env (optional) | `CRM_PUSH_LIMIT` | max rows pushed per run (default `100`). |

### CRM hand-off (crm_push.py)
After scoring, `ingest.py` (and the daily `reingest_news.py`) call
`crm_push.run()`, which sends high-fit, **pre-start** (`NOT_YET_RECRUITING`)
trials to the CRM's `POST /api/ingest/pipeline-lead`. Each pushed trial is
stamped (`crm_lead_id`, `crm_pushed_at`) so it is never pushed twice. The CRM
owns outreach (dedup, Seamless.AI email enrichment, send + tracking); see the
aicure-crm README. Unconfigured = silent no-op, so this is safe to ship dark.

## Load-bearing constraints (do not break)
- **Single writer.** `desiredCount=1` + min0/max100. Two tasks = divergent SQLite + double-fired schedulers.
- **EFS is the source of truth after first boot.** The seed copies only onto an empty volume;
  image rebuilds do **not** update live data. A bulk reload is a deliberate one-off (replace the
  EFS file, or run a one-off ingest task) — daily incremental adds just work, which is the point.
- **Non-root uid 10001 ↔ EFS access-point uid 10001.** A mismatch denies the first-boot `cp` to `/data`.
- **`AICURE_APP_PASSWORD` is required** on any internet-facing deploy, or the whole UI + API is open.
- **`git lfs pull` before every build**, or the image ships a 331 MB LFS pointer (which `/healthz` catches).
- **The in-app scheduler is now always-on** (Fargate, desired=1): 06:00 UTC news refresh + 07:00 rescore
  run *alongside* the 13:00 GitHub-cron refresh+send. Not a double-send (06:00 only refreshes) — just
  confirm you want both jobs.

## Verification
Tests:
```
.venv/bin/python -m pytest backend -q
```
NFS-safe pragmas (expect journal_mode=delete and mmap_size=0):
```
cd backend && AICURE_DB_PATH=/tmp/aic_test.db AICURE_DB_NETWORK_FS=1 ../.venv/bin/python -c "import db; c=db.get_connection(); print(c.execute('PRAGMA journal_mode').fetchone(), c.execute('PRAGMA mmap_size').fetchone())"
```
Cutover (against the ALB DNS, before flipping DNS): `/healthz` returns 200 (auth-exempt);
the browser prompts once for Basic creds, then the SPA at `/` and `/trials?page=1` load
(creds are cached); `POST /admin/send-news-digest?refresh=false` with the `X-Admin-Key`
header bypasses the gate → confirm the email lands.

## Background
Resend domain benjaminhelfand.com is verified under the **helfandother@gmail.com** account;
the send key must come from there and the `from` address must be on that domain. The original
full plan (deeper rationale on every choice) was produced in plan mode — this file is the
durable, self-contained version.
