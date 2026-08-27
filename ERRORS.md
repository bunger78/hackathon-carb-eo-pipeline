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
