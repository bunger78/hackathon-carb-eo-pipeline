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

## 2026-08-27 — Cloud Run service URL 404 (unresolved ~1 hr, not a config error)

Both service URLs return Google-frontend generic 404s from the local network ~1 hr after deploy; service is Ready, ingress=all, both URLs listed active, DNS consistent across resolvers (34.143.72-79.2), no request ever reaches Cloud Run logs. Ruled out: auth (would be 401/403), ingress, DNS poisoning, client TLS/SNI (PowerShell and Python agree). Working theory: slow edge/GFE route propagation for a brand-new project, possibly VPN/geo egress related (resolver returns non-US anycast IPs). Scheduler calls originate inside Google's network and may be unaffected. Next steps if still dead: test from Cloud Shell (note: `gcloud cloud-shell ssh` on Windows hits an interactive PuTTY host-key prompt — pre-seed the key or use another remote vantage), then Google issue tracker.
