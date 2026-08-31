# ERRORS.md — approaches that took >2 attempts

Check before retrying similar tasks.

## 2026-08-27 — Cloud Run Job command override (3 attempts)

**What didn't work:**
1. `gcloud run jobs deploy ... --command python --args backfill.py` — all 4 tasks died with "Application exec likely failed". A bare command override on a buildpack image bypasses the buildpack launcher, so the runtime layer's `python` is not on PATH.
2. Procfile `backfill:` process type + `--command /cnb/process/backfill` **from Git Bash** — Git Bash (MSYS) silently rewrote the leading-slash argument to `C:/Program Files/Git/cnb/process/backfill` before gcloud saw it. The job then exec'd a Windows path inside a Linux container.

**What worked:** Procfile process type (`backfill: python backfill.py`) + `gcloud run jobs update --command "/cnb/process/backfill"` **run from PowerShell** (no MSYS path mangling).

**Notes for next time:**
- On Windows, any gcloud/docker/etc. argument starting with `/` must go through PowerShell, or prefix Git Bash with `MSYS_NO_PATHCONV=1`.
- Verify what gcloud actually stored: `gcloud run jobs describe --format="value(...containers[0].command)"` — the mangled path was visible there immediately; reading it first would have saved attempt 2's rebuild.
- For buildpack images, define every entrypoint as a Procfile process type and exec `/cnb/process/<type>`; never point --command at a bare interpreter.
- `gcloud run jobs update` has no `--clear-args`; passing `--args ""` from PowerShell also fails (PS eats the empty string). Updating `--command` alone left the args empty as needed.

## 2026-08-27 — "Cloud Run URL doesn't route" that was actually a reserved path (~10 hrs elapsed, many attempts)

**What didn't work:** treating GET /healthz -> Google-style 404 as broken URL routing. Chased: propagation waits, DNS comparisons across resolvers, client TLS/SNI (PowerShell vs Python), no-op revision update, Cloud Build vantage test, full service delete+recreate, service rename (carb-pipeline -> carb-api), temporary allUsers binding. All 404. A stock hello-container probe "worked" — misleadingly (see below).

**Root cause:** **`/healthz` is reserved by Google's frontend on `*.run.app`** — GFE answers 404 itself and never forwards the request (hence zero request logs, which read as "not routed"). Two coincidences hid it: (1) Google's hello container serves a *pixel-perfect imitation of GFE's 404 page* for unknown paths, so the hello probe at `/` "proving routing works" proved nothing about /healthz; (2) FastAPI's JSON 404 on `/` was the first response that visibly came from our app.

**What worked:** requesting `/openapi.json` — 200 from our own app with /healthz in its route table, same second /healthz returned the Google page. Endpoint renamed to `/health`. Every deployment had been serving correctly the entire time.

**Notes for next time:**
- On *.run.app, never name a health endpoint `/healthz`. Use `/health`.
- "No request logs + generic Google 404" ≠ broken routing — request a path you KNOW the app serves (`/openapi.json` for FastAPI) before concluding anything about routing.
- The Cloud Run hello container fakes Google's 404 page; never use it to differentiate GFE-vs-app responses.
- Side effects kept: service is now named carb-api (scheduler rewired by deploy.sh); the delete/recreate cycles were harmless.
- Sub-lesson from the same session: `gcloud cloud-shell ssh` on Windows hits an interactive PuTTY host-key prompt in non-interactive shells; a Cloud Build step with a python one-liner is the reliable inside-Google vantage.

## Queue live-lock — shard pre-filter vs. the lease queue

`backfill.py`'s per-shard worker originally filtered work items by a hash of the EO number before claiming, so each shard would only touch "its own" items. In practice the filter fought the lease queue: a shard would claim an item outside its filter, immediately release it back to pending, then reclaim the very same oldest item on its next loop iteration, because `claim_next` always returns the oldest claimable item first. Every worker ended up bouncing off the same foreign item at the head of the queue instead of ever advancing past it, stalling the whole backfill run. Fix: dropped the shard pre-filter entirely and trusted `claim_next`'s transactional lease (a compare-and-swap on `status`/`lease_expires`) to give each worker exclusive ownership of whatever it claims — any worker can process any item, so there's no head-of-queue item to bounce off of. Note for next time: a client-side filter layered on top of a lease-based queue can reintroduce the exact contention the queue was built to avoid; if sharding is needed again, shard the claim query itself, not a post-claim release.

## Vertex 429 storms at high concurrency

Running several concurrent Cloud Run Job workers against Vertex AI produced bursts of `429 RESOURCE_EXHAUSTED` once concurrent request volume crossed the project's quota. Each 429 failed its work item's extraction attempt, and once an item exhausted `max_attempts` it sat "failed" until a human noticed and retried it manually — with hundreds of items in flight, that meant a growing pile of jobs failed only because of a transient quota blip, not a real data problem. Fix: added `agents/healer.py`, a self-heal stage that runs at the start of every daily run and requeues any "failed" item whose `last_error` classifies as transient (429/500/503, `RESOURCE_EXHAUSTED`/`DEADLINE_EXCEEDED`/`UNAVAILABLE`) back to pending, capped at a per-item heal count so a permanently-flaky EO still eventually surfaces to a human instead of looping forever. Note for next time: a transient-error classifier is only as good as the error text it's given — it needs the real underlying error string, not a generic failure message or bare identifier standing in for it.

## Cloud Scheduler's default 180s deadline killing long runs

Cloud Scheduler's default HTTP job `attempt-deadline` is 180 seconds. The pipeline's `/run` endpoint processes a queue of work items in a loop and can legitimately run for many minutes on a busy day, so the scheduler was killing the HTTP request — and whatever work item was mid-flight — well before a run had a chance to finish or even summarize cleanly. Fix: two changes together, not one — `infra/deploy.sh` raises the scheduler job's `--attempt-deadline` to 1800s, and separately the pipeline enforces its own in-process graceful time cap (`settings.run_time_cap_seconds`, 1500s) so a run stops claiming new work and finishes cleanly well inside every timeout in the chain, instead of being killed mid-item. Note for next time: raising the caller's timeout alone isn't enough for a long-running loop — the callee needs its own internal cutoff with headroom under every timeout upstream of it, not just the outermost one.
