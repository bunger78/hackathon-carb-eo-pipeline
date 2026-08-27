# CarbLegal Dashboard Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** The hosted judging URL — an Astro SSR dashboard on Cloud Run over the pipeline's Firestore state: overview + live agent console, run traces, review queue with approve→resume, EO browser with legacy diff + supersession lineage, and vehicle search.

**Architecture:** Astro 5 SSR (Node adapter) as service `carb-dash` on Cloud Run, public read (`--allow-unauthenticated`). Server-side Firestore reads via ADC (SA `sa-dash`: datastore.viewer + run.invoker on carb-api). React islands poll JSON endpoints for liveness (no websockets). Mutations hold zero dashboard secrets: the admin token is typed in the UI, forwarded via a server proxy to `carb-api` with a metadata-server OIDC token — the pipeline's existing fail-closed check is the single authority.

**Tech Stack:** Astro 5 + @astrojs/node (standalone) + @astrojs/react, TypeScript, @google-cloud/firestore, vitest for pure-logic units. No CSS framework — one hand-written stylesheet (Lee's Astro home turf; legibility over design systems).

**Spec:** docs/superpowers/specs/2026-08-26-carb-eo-agent-pipeline-design.md §6 (+ §13 presentation constraints; agent console, receipt stat, and lineage view are REQUIRED, not optional).

## Global Constraints

- Zero secrets in the dashboard: no API keys, no admin token stored server-side; ADC only. The admin token exists only in the operator's browser (localStorage) and in transit.
- All Firestore access is read-only except via the carb-api proxy. `sa-dash` gets `roles/datastore.viewer` + `roles/run.invoker` (carb-api), nothing else.
- Every page renders sensibly with EMPTY collections (backfill may be mid-flight during dev): no unguarded `[0]`, no assumption a run/extraction exists.
- Collections (read contract, from pipeline/core/db.py): `eos` (doc id = EO number; state, pdf_url, gcs_uri, supersedes[], superseded_by, match_count, first_seen), `extractions` ({eo}_v{n}; fitment rows carry part_numbers[]), `legacy_extractions` (doc id = EO number), `matches` ({eo}_{vehicleId}; denormalized category/device_name/manufacturer/part_numbers, vehicle_id, confidence), `vehicles` (year/make/model/trim/engine fields incl. induction), `review_queue` (status open|approved|rejected, eo_number, reason, agent_notes, payload), `runs` + subcollection `events` (ts, agent, action, eo), `work_items` (status pending|in_progress|done|failed).
- Money/number formatting: cost as `$X.XX`; counts with thousands separators; timestamps rendered in **America/Los_Angeles**.
- `npm run build` + `npm run test` green is the gate for every task; commit per task.
- Model/branding strings: "Gemini 3.7 Flash", "CarbLegal". Footer on every page: "Not affiliated with the California Air Resources Board. Data derived from public CARB Executive Orders."

## File Structure

```
dashboard/
  package.json  astro.config.mjs  tsconfig.json  vitest.config.ts
  src/
    lib/db.ts          # Firestore singleton + typed readers (ALL queries live here)
    lib/format.ts      # ts/cost/count formatting (pure)
    lib/diff.ts        # legacy-vs-agent diff computation (pure, vitest-covered)
    lib/proxy.ts       # OIDC service-to-service call to carb-api
    layouts/Base.astro # nav, footer, styles
    styles/global.css
    components/        # React islands: Console.tsx, RunNow.tsx, ReviewActions.tsx, VehiclePicker.tsx
    pages/
      index.astro                 # T3 overview + console + receipt
      runs/[id].astro             # T4 run detail
      eos/index.astro  eos/[eo].astro    # T5 browser + detail (diff, lineage)
      review/index.astro review/[id].astro  # T6 queue
      vehicles.astro              # T7 search
      api/feed.ts api/overview.ts api/run-now.ts api/review-action.ts
      api/vehicle-facets.ts api/vehicle-parts.ts
```

Pipeline touch (Task D1 only): `pipeline/main.py` (+1 endpoint), `pipeline/agents/reviewer.py` (new, ~40 lines), tests.

---

### Task D1: Pipeline review-resolution endpoint (prerequisite interface)

**Files:** Create `pipeline/agents/reviewer.py`, `pipeline/tests/test_reviewer.py`; Modify `pipeline/main.py`.

**Interfaces:** Produces `POST /admin/resolve-review` on carb-api — body `{"review_id": str, "action": "approve"|"reject", "corrections": {field: value} | null}`, header `X-Admin-Token` (same fail-closed gate as /admin/run-now). approve: load review doc + latest extraction for its EO → apply shallow field `corrections` to the extraction doc (write as NEW version via `repo.next_extraction_version`/`write_extraction`) → `run_matching(...)` → eo state `complete` → review doc `{"status":"approved","resolved_at":time.time()}`. reject: eo state `failed`, review doc status `rejected`. Returns `{"review_id","action","matches": n|0}`.

- [ ] **Step 1: failing tests** in `pipeline/tests/test_reviewer.py` — reuse the fake repo/LLM/index pattern from `tests/test_runner.py`:

```python
import time
import reviewer_fixtures  # if the runner fakes are importable, use them; else copy the ~20-line fakes inline
from agents.reviewer import resolve_review

def test_approve_applies_corrections_and_matches(fake_deps):
    fake_deps.repo.reviews["r1"] = {"eo_number": "D-100-1", "status": "open"}
    fake_deps.repo.extractions[("D-100-1", 1)] = {"eo_number": "D-100-1", "confidence": 0.5, "fitment": []}
    out = resolve_review(fake_deps, "r1", "approve", {"confidence": 0.9})
    assert fake_deps.repo.extractions[("D-100-1", 2)]["confidence"] == 0.9
    assert fake_deps.repo.eos["D-100-1"]["state"] == "complete"
    assert fake_deps.repo.reviews["r1"]["status"] == "approved"

def test_reject_marks_failed(fake_deps):
    fake_deps.repo.reviews["r1"] = {"eo_number": "D-100-1", "status": "open"}
    out = resolve_review(fake_deps, "r1", "reject", None)
    assert fake_deps.repo.eos["D-100-1"]["state"] == "failed"

def test_unknown_review_raises(fake_deps):
    import pytest
    with pytest.raises(KeyError):
        resolve_review(fake_deps, "nope", "approve", None)
```

(Adapt the fake-deps fixture from test_runner.py's fakes; add `reviews`/`get_review`/`update_review` to the fake repo mirroring db.py additions below.)

- [ ] **Step 2: implement.** `pipeline/core/db.py` gains `get_review(review_id)` / `update_review(review_id, fields)` (mirror `add_review`'s collection). `pipeline/agents/reviewer.py`:

```python
import time
from schemas.extraction import Extraction
from agents.matchmaker import run_matching

def resolve_review(deps, review_id: str, action: str, corrections: dict | None) -> dict:
    review = deps.repo.get_review(review_id)
    if review is None:
        raise KeyError(f"review {review_id} not found")
    eo = review["eo_number"]
    if action == "reject":
        deps.repo.upsert_eo(eo, {"state": "failed"})
        deps.repo.update_review(review_id, {"status": "rejected", "resolved_at": time.time()})
        return {"review_id": review_id, "action": action, "matches": 0}
    version = deps.repo.next_extraction_version(eo)
    latest = deps.repo.get_extraction(eo, version - 1) if version > 1 else None
    if latest is None:
        raise KeyError(f"no extraction for {eo}")
    doc = {**latest, **(corrections or {})}
    deps.repo.write_extraction(eo, version, doc)
    run_id = deps.repo.create_run("review-resolve")
    ex = Extraction.model_validate(doc)
    result = run_matching(deps.llm, deps.repo, deps.budget, eo, ex, deps.index, run_id)
    deps.repo.upsert_eo(eo, {"state": "complete"})
    deps.repo.update_review(review_id, {"status": "approved", "resolved_at": time.time()})
    deps.repo.finish_run(run_id, {"status": "ok", "reviewed": eo})
    return {"review_id": review_id, "action": action, "matches": result["matches"]}
```

`pipeline/main.py` endpoint (same token gate as run_now):

```python
from pydantic import BaseModel

class ReviewResolution(BaseModel):
    review_id: str
    action: str  # "approve" | "reject"
    corrections: dict | None = None

@app.post("/admin/resolve-review")
def admin_resolve_review(body: ReviewResolution, x_admin_token: str = Header(default="")):
    if not ADMIN_TOKEN or x_admin_token != ADMIN_TOKEN:
        raise HTTPException(401)
    if body.action not in ("approve", "reject"):
        raise HTTPException(422)
    from agents.reviewer import resolve_review
    try:
        return resolve_review(build_deps(), body.review_id, body.action, body.corrections)
    except KeyError as e:
        raise HTTPException(404, str(e))
```

- [ ] **Step 3:** `cd pipeline && py -3 -m pytest -q` → all green (61+3). **Step 4:** commit `feat: review-resolution endpoint (approve/reject -> rematch)`.

### Task D2: Astro scaffold + data layer + deploy config

**Files:** Create everything under `dashboard/` listed in File Structure except pages beyond index placeholder; Modify `infra/deploy.sh` (append carb-dash deploy + sa-dash IAM), `infra/setup.sh` (create sa-dash).

**Interfaces:** Produces `lib/db.ts` readers consumed by every later task: `overviewCounts()`, `recentRuns(n)`, `runWithEvents(id)`, `searchEos(q, filters, cursor)`, `eoDetail(eo)`, `legacyFor(eo)`, `openReviews()`, `reviewDetail(id)`, `vehicleFacets()`, `vehiclesMatching(facets)`, `partsForVehicle(vehicleId, category?)`, `totalBackfillCost()`. All return plain serializable objects; every reader tolerates empty collections.

- [ ] Step 1: scaffold. `package.json` (name carb-dash; scripts: dev/build/preview/test=vitest run; deps: astro ^5, @astrojs/node ^9, @astrojs/react ^4, react ^19, react-dom ^19, @google-cloud/firestore ^7; devDeps: typescript, vitest ^3, @types/react). `astro.config.mjs`: `output: 'server'`, node adapter standalone, react(). `start` script: `node ./dist/server/entry.mjs` (buildpack contract; PORT is honored by the adapter via HOST/PORT env).
- [ ] Step 2: `lib/db.ts` — Firestore singleton `new Firestore({projectId: process.env.PROJECT_ID || 'carblegal'})`; implement the readers with the exact queries: counts via `.count()` aggregates per state; `searchEos` — by EO prefix (documentId range query), by part number (`fitment` array queries are impossible server-side → query `matches` where `part_numbers array-contains q` then group by eo), by manufacturer (`eos where manufacturer ==`); `eoDetail` — eo doc + latest extraction (query extractions where eo_number ==, order by version desc limit 1) + lineage (follow supersedes[]/superseded_by both directions, max depth 5) + last 20 events (collection-group needs index — instead store per-run; query runs subcollections is expensive → skip trace-history-across-runs in detail, show link to latest run instead); `partsForVehicle` — `matches where vehicle_id == id` (+ optional category filter → composite index already exists).
- [ ] Step 3: `lib/format.ts` + `lib/diff.ts` (pure): diff takes legacy doc + extraction doc → `{fields: [{name, legacy, agent, changed}], partNumbers: {added: [], removed: [], kept: []}, fitmentCounts: {legacy, agent}}`. Vitest: 3 cases (both present; legacy missing; identical → no changes).
- [ ] Step 4: `lib/proxy.ts`:

```ts
export async function callPipeline(path: string, body: unknown, adminToken: string) {
  const base = process.env.PIPELINE_URL!; // e.g. https://carb-api-cx5tppcuda-uc.a.run.app
  const meta = `http://metadata.google.internal/computeMetadata/v1/instance/service-accounts/default/identity?audience=${base}`;
  let idToken = '';
  try {
    idToken = await (await fetch(meta, { headers: { 'Metadata-Flavor': 'Google' } })).text();
  } catch { /* local dev: no metadata server; pipeline will 403 */ }
  const res = await fetch(`${base}${path}`, {
    method: 'POST',
    headers: { Authorization: `Bearer ${idToken}`, 'X-Admin-Token': adminToken, 'Content-Type': 'application/json' },
    body: JSON.stringify(body ?? {}),
  });
  return new Response(await res.text(), { status: res.status });
}
```

- [ ] Step 5: `layouts/Base.astro` + `styles/global.css` (simple dark-on-light, mono numerals, status color tokens: complete=green, needs_review=amber, failed=red, superseded=gray) + placeholder `pages/index.astro` rendering `overviewCounts()`.
- [ ] Step 6: infra: `setup.sh` append sa-dash creation + `roles/datastore.viewer`; `deploy.sh` append:

```bash
gcloud run deploy carb-dash --source dashboard --region $REGION \
  --service-account "sa-dash@$P.$SFX" --allow-unauthenticated \
  --memory 512Mi --set-env-vars "PROJECT_ID=$P,PIPELINE_URL=$URL"
gcloud run services add-iam-policy-binding $SERVICE --region $REGION \
  --member="serviceAccount:sa-dash@$P.$SFX" --role=roles/run.invoker -q
```

- [ ] Step 7: `npm install && npm run build && npm run test` green → commit `feat: dashboard scaffold, data layer, deploy config`.

### Task D3: Overview + agent console + receipt (the cold-open page)

**Files:** Create `pages/index.astro` (real), `pages/api/feed.ts`, `pages/api/overview.ts`, `pages/api/run-now.ts`, `components/Console.tsx`, `components/RunNow.tsx`.

- [ ] SSR overview: counts-by-state tiles; **receipt stat hero**: "≈{eos complete} EOs · {sum extractions} extractions · ${totalBackfillCost} total Gemini spend" (from runs where trigger startswith backfill + daily runs; compute server-side, cache 60s in module scope); last run summary + next scheduled ("daily 6:00 AM PT"); open review count.
- [ ] `api/feed.ts`: latest run doc → last 50 events ordered by ts desc → `[{ts, agent, action, eo}]` JSON. `api/overview.ts`: the tiles as JSON.
- [ ] `Console.tsx` island: polls `/api/feed` every 3s, renders a scrolling terminal-style feed (`[06:00:12] scout    discovered   D-800-1`), pulsing dot when events advanced since last poll. This is the demo cold-open — visual polish budget goes HERE.
- [ ] `RunNow.tsx` island: admin token input (localStorage key `carblegal_admin`), button POST `/api/run-now` (which calls `callPipeline('/admin/run-now', {}, token)`), renders returned summary inline; 401 → "bad token".
- [ ] Build+test green; commit `feat: overview, live agent console, run-now`.

### Task D4: Run detail

**Files:** Create `pages/runs/[id].astro`; extend `lib/db.ts` (`runWithEvents`).

- [ ] Run header (trigger, started/finished, status, cost, tok in/out) + events grouped by EO into per-EO timelines (scout→extractor→auditor→matchmaker rows with elapsed deltas); EO links to /eos/{eo}. Runs index list on overview already links here.
- [ ] Empty/missing run id → friendly 404. Build green; commit `feat: run detail traces`.

### Task D5: EO browser + detail (diff + lineage)

**Files:** Create `pages/eos/index.astro`, `pages/eos/[eo].astro`; extend db.ts.

- [ ] Index: search box (EO # prefix | part number | manufacturer — radio), status filter chips (incl. superseded), paged 50/page.
- [ ] Detail: header (state badge, manufacturer, device, category, confidence, PDF link → `pdf_url` on arb.ca.gov, supersedes/superseded-by); **lineage chain view**: horizontal chain `D-161-136 → D-161-149 → …` following both directions (each node linked, current bolded, superseded grayed) — this is the demo-centerpiece shot; fitment table (year-range/make/model/trim/engine + per-row part numbers); **legacy-vs-agent diff panel** via `lib/diff.ts`: field table with changed rows highlighted + part-number added/removed/kept pills + fitment row counts ("legacy 0 associations → agent 36 rows"); link to latest run trace.
- [ ] Handles: no legacy doc (post-legacy EO), no extraction yet (state discovered/failed), empty fitment. Build green; commit `feat: EO browser, legacy diff, supersession lineage`.

### Task D6: Review queue

**Files:** Create `pages/review/index.astro`, `pages/review/[id].astro`, `pages/api/review-action.ts`, `components/ReviewActions.tsx`.

- [ ] Index: open items (reason chip, EO, agent_notes first line, age); resolved section collapsed below.
- [ ] Detail: reason + full agent_notes + payload rendered as definition list + PDF link + link to EO page; `ReviewActions.tsx` island: Approve / Reject buttons + optional JSON corrections textarea (validated client-side as JSON, shallow field:value), admin token from same localStorage key → POST `/api/review-action` → `callPipeline('/admin/resolve-review', {review_id, action, corrections}, token)`; success → render returned match count + "resumed" banner.
- [ ] Build green; commit `feat: review queue with approve/reject resume`.

### Task D7: Vehicle search

**Files:** Create `pages/vehicles.astro`, `pages/api/vehicle-facets.ts`, `pages/api/vehicle-parts.ts`, `components/VehiclePicker.tsx`.

- [ ] `api/vehicle-facets.ts`: one full `vehicles` scan on first request → `{years: [...], makesByYear: {..}, modelsByYearMake: {..}, trims: {..}, engines: {..}}` cached in module memory (~25k docs once per instance; acceptable, note in code).
- [ ] `VehiclePicker.tsx`: cascading selects year→make→model→trim→engine from facets; on complete selection → `/api/vehicle-parts?vehicle_id=..` (facets payload carries vehicle_id per leaf) → categorized parts list: category sections, each part row = device name, manufacturer, EO link, per-vehicle part numbers, confidence badge (≥0.9 green / ≥0.75 amber / else red).
- [ ] SSR shell + island; empty-match message ("No CARB-exempt parts indexed for this vehicle"). Build green; commit `feat: vehicle search`.

### Task D8: Deploy + smoke + evidence

**Files:** none new (deploy.sh from D2).

- [ ] `bash infra/setup.sh carblegal` delta (sa-dash) then `bash infra/deploy.sh carblegal` → carb-dash URL live.
- [ ] Smoke (controller): overview renders real counts; console feed moves during a manual run; one review approve round-trips (against a real open review item); vehicle search returns parts for a known-good vehicle (pick from matches); EO diff page for D-338-98 (the "Unknown Device" star) renders.
- [ ] Record the URL in README + TODO; commit any smoke fixes as `fix:` commits.

## Execution notes

- Order: D1 → D2 → D3..D7 (sequential, one implementer at a time) → D8 controller.
- D1 is Python (pipeline conventions, cheap model OK given full code above). D2–D7 are TypeScript/Astro — use a mid-tier implementer; D3 and D5 carry the demo weight (review with care).
- The pipeline's ADK2 graph work (Task 21) may land in parallel calendar-wise but NEVER dispatch two implementers at once (shared git index — see ledger PROCESS RULE).
