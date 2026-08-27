# Design: CarbLegal (CARB EO Agent Pipeline)

**Project name (decided 2026-08-26): CarbLegal** — tagline: "CARB's database, inverted: every street-legal part, searchable by your exact car — kept current by an autonomous agent."

**Date:** 2026-08-26 · **Status:** Approved (all sections reviewed in brainstorming session)
**Hackathon:** All Things Agentic Hackathon (Devpost/Google) · **Track:** Taskmaster · **Deadline:** Aug 31, 2026 5:00pm PDT
**Team:** Lee (solo, ~4 hrs/day) + Claude

## 1. Goal

Replace the regex/OCR extraction pipeline behind SmogLegal with an autonomous multi-agent system on Google Cloud that: discovers new CARB Executive Orders daily, extracts structured parts/fitment data from PDFs with Gemini, audits its own output, matches parts to real vehicles, escalates only genuine ambiguity to a human review queue, and serves the result through a dashboard — including the query the legacy system cannot answer: *exact part numbers per vehicle*.

Success criteria:
1. Daily scheduled run processes new CARB EOs end-to-end with no human involvement except review-queue decisions.
2. Golden-set extraction accuracy measurably exceeds the regex baseline (esp. part numbers and PN↔vehicle association).
3. Full 6,055-EO backfill completes within the ~$150 credit budget.
4. Submission deliverables: hosted dashboard URL, reproducible README spin-up, architecture diagram, 4-min live demo video proving Google Cloud backend.

## 2. Hackathon constraints (must-use)

- Gemini 3.5+ via Gemini API or Vertex AI → **Gemini 3.5 Flash via Vertex AI** (ADC auth, no API keys; newest available Flash tier to be confirmed day 1 in-region before committing the model ID).
- ≥1 Google agent framework → **ADK (Python)**; fallback **GenAI SDK** (also qualifying) if ADK resists batch-style invocation. Decision gate: end of build day 1.
- ≥1 GCP infra service → Cloud Run (+Jobs), Scheduler, Firestore, Storage, and supporting services below.

## 3. Architecture

Two Cloud Run services + one Cloud Run Job, event flow:

```
Cloud Scheduler (daily 06:00 PT, OIDC) ──► Pipeline service (Python, ADK, FastAPI shell)
    Scout ► Extractor ► Auditor ► Matchmaker          │
        │         └───── Gemini 3.5 Flash (Vertex AI, gs:// PDF input)
        ▼                                              ▼
  Cloud Storage (PDFs)                        Firestore (system of record)
                                                       ▲
Backfill: Cloud Run Job, sharded by TASK_INDEX, resumable (state in Firestore)
Dashboard service (Astro SSR, Node): public read; admin token (Secret Manager) gates mutations
Observability: Cloud Trace spans + structured Cloud Logging + Firestore run events
```

Service roster and one-line justifications: see README (§Architecture). Deliberately excluded, on the record: Pub/Sub, BigQuery, GKE, Terraform, Vertex Agent Engine/Memory Bank/Model Armor.

IAM (least privilege): `sa-pipeline` (Vertex invoke, Firestore r/w, GCS r/w), `sa-dashboard` (Firestore r/w limited to review/admin writes, Secret Manager accessor), `sa-scheduler` (run.invoker on the pipeline's /run endpoint only).

## 4. Data model (Firestore)

- **`eos/{eoNumber}`** — registry + state machine (`discovered → extracting → auditing → matching → complete | needs_review | failed`) + accepted extraction summary (device family, manufacturer, category, part_numbers[], status incl. `superseded`, confidence). Part identity merged into EO doc (verified 1:1 in legacy data; CARB certifies one device family per EO).
- **`extractions/{eo}_v{n}`** — full versioned extraction: EO metadata, `supersedes[]`, fitment rows **each with `part_numbers[]`**, per-section confidence, audit verdict, prompt+schema version, token/cost accounting.
- **`legacy_extractions/{eo}`** — one-time import of regex output for before/after diffs and improvement stats.
- **`vehicles/{id}`** — 24,902 trims seeded from legacy SQLite; loaded into worker memory for matching (never per-row queries).
- **`matches/{eo}_{vehicleId}`** — confidence tier (`exact|high|medium|generic`), method (`deterministic|gemini_resolved` + one-line rationale), per-vehicle `part_numbers[]`, denormalized `category/device_name/manufacturer` → vehicle search is one indexed query (`vehicle_id ==`, optional `category ==` composite index); detail view fetches the EO doc.
- **`work_items/{id}`** — leased queue (claim + expiry), `stage`, `attempts` (cap 3), `last_error`. Dashboard live-renders.
- **`review_queue/{id}`** — reason (`low_confidence|ambiguous_match|validation_failure`), agent explanation, proposed payload, human action (`approved|corrected|rejected`); action re-triggers downstream stages.
- **`runs/{id}` + `/events`** — trigger type, counts, cost; per-EO reasoning timeline (events batched to respect 1 write/sec/doc).

Consistency rule: matches are written only by the Matchmaker after an accepted extraction; re-extraction re-runs matching for that EO and overwrites (deterministic IDs). No second writer → denormalization cannot drift.

Supersession model (verified against CARB documents): CARB never edits published EO bytes; revisions arrive as new EO numbers that "supersede and cancel" predecessors. Extractor captures `supersedes[]`; pipeline marks predecessors `superseded`. No re-download/re-hash machinery exists. SHA-256 recorded once at download for provenance only.

## 5. Agent behaviors

**Scout** (no LLM): fetch Power BI listing (port of proven `download_eos.py`, rate-limited) → diff vs registry → download new PDFs to GCS → create work items + run doc.

**Extractor** (Gemini, temperature 0, response-schema JSON, PDF via `gs://` URI): strategy ladder = (1) native PDF → (2) page-image re-render at high DPI (rescues scanned EOs). No page-chunking (corpus max ≈ 18 pages; limit is 1,000). Self-reported per-section confidence + illegible-page flags. Prompts ordered static-prefix-first for implicit caching. Token/cost logged per call.

**Auditor**: stage 1 deterministic (year ranges, displacement sanity, PN pattern validity, make ∈ known-makes, dedupe, registry consistency — free); stage 2 legacy comparison (divergence raises scrutiny, never auto-fails); stage 3 Gemini critique (fresh context, skeptic prompt, page-referenced discrepancies) — **selective**: only on cheap-signal disagreement or low confidence (~30% expected) + 5% random QA sample; always for daily deltas. Verdicts: accept / fix-once-then-re-audit / escalate with explanation. 3 strikes → `failed`, visible, never silent.

**Matchmaker**: deterministic port of `match_engines.py` (bidirectional model match, engine COALESCE fallback, confidence tiers) against in-memory vehicle set → auto-write `exact/high/medium`. Zero-match and `generic` rows batched per EO to Gemini: candidates in, applicable vehicles out, mandatory one-line rationale per decision, threshold below which → review queue.

Cross-cutting: leases prevent double-processing; deterministic doc IDs make re-runs overwrite; 429/5xx retry with backoff; per-run token budget cap halts gracefully (dashboard shows `budget_exceeded`); Cloud Monitoring alert on run failure.

## 6. Dashboard (Astro SSR, Cloud Run — the hosted judging URL)

1. **Overview**: counts by state, last-run summary, cost meter, next scheduled run, **Run now** (admin), live queue view (React island, polling).
2. **Run detail**: per-EO reasoning timeline from run events.
3. **Review queue**: agent explanation + proposed data + PDF (signed GCS URL); approve/correct/reject → pipeline resumes downstream.
4. **EO browser**: search (EO #, part number via array-contains, manufacturer), filters (category/status incl. superseded); detail = full extraction, per-row PNs, **legacy-vs-agent diff**, trace history, PDF link.
5. **Vehicle search**: year/make/model/trim/engine cascade → categorized parts with confidence badges and per-vehicle part numbers.

Auth: public read; mutations require admin token from Secret Manager. No accounts.

## 7. Repository layout & deployment

Layout: per README (§Planned Repository Layout). Deployment: idempotent `infra/setup.sh` (APIs, Firestore + PITR flag, bucket, SAs, IAM bindings, secret, Scheduler) + `infra/deploy.sh` (`gcloud run deploy --source` ×2 + job). `firestore.indexes.json` committed. CI: GitHub Actions pytest (deterministic tests only; golden eval is local — it spends tokens). Repo public; no secrets exist to leak.

## 8. Evaluation & cost

**Golden set**: 25 EOs, stratified (modern tables / pre-1990 text / image-only / part-number families e.g. D-269-30). Claude drafts expected JSON from PDFs; Lee reviews (~2 hrs). Pytest harness scores per-field accuracy, regex baseline vs agent. All demo claims come from this + corpus before/after stats.

**Cost model** (measured corpus: 6,055 PDFs, ≈23,000 pages, avg 3.8/EO; verified pricing $1.50/$9.00 per M in/out, 258 tok/page): backfill ≈ $23 in + ~$109 out (extraction) + ~$19 (selective critique) + ~$25 (resolution + dev) ≈ **$100–150**. Dominant uncertainty: avg output tokens/EO — measured in day-1 spike. Batch inference (–50%) is a gate decision if projection exceeds ~$150. Dailies ≈ cents; all infra scales to zero.

## 9. Security & threat model

No API keys (ADC); least-privilege SAs; single secret in Secret Manager. Prompt injection via PDFs acknowledged: bounded by arb.ca.gov-only sourcing, schema-constrained output, output-never-instructions, Auditor re-derivation; residual risk documented, not engineered away. Downloader rate-limited.

## 10. Risks, gates, fallbacks

| Risk | Gate/date | Fallback |
|---|---|---|
| Vertex quota too low for backfill | Day 1 console check + increase request | Throttled backfill over 2 nights; batch API |
| Gemini quality on hard PDFs | Day 1 spike, 10 gnarly PDFs | Redesign ladder / model tier with 4+ days left |
| ADK fights batch invocation | End of build day 1 | GenAI SDK (still satisfies requirement) |
| Cost overrun | Day-1 measured output tokens; backfill go/no-go | Batch API (–50%); corpus subset with disclosed count |
| Schedule slip | Pre-agreed scope-cut ladder | Cut order: Gemini match-resolution → diff view → CI. Core (daily loop, extraction, review, vehicle search, traces) protected |
| Demo-day emptiness | Hold-back staging (real EOs withheld from registry) | Re-record window on Aug 31 morning |

## 11. Timeline (revised: 2 full rework days reserved)

Feature-complete target is EOD Aug 28 — deliberately aggressive so that Aug 29–30 exist as genuine rework capacity, not planned feature work. Lee's ~4 hrs/day = reviews, GCP console actions, golden-set verification, demo recording; Claude builds.

| Date | Focus | Exit criteria |
|---|---|---|
| **Aug 26 (eve)** | De-risk | Credit claimed; quota checked, increase filed if low; model ID verified in-region; 10-PDF spike scored (quality + avg output tokens); cost model updated; golden drafts started |
| **Aug 27** | Build I: core loop | One EO flows PDF→extract→audit→Firestore locally; ADK go/no-go decided; seeds run (vehicles, legacy); golden drafts to Lee |
| **Aug 28** | Build II: cloud | Daily path deployed and runs end-to-end in cloud; Matchmaker + tests green; golden eval v1 numbers; dashboard overview + traces live; **backfill launched overnight**; batch-vs-online decided |
| **Aug 29** | **Rework I** | Backfill results absorbed; extraction fixes from golden eval; dashboard completed (review queue, vehicle search, diff); slippage from build days lands here |
| **Aug 30** | **Rework II + record** | Feature freeze 12:00; hold-back staged; rehearsal; **4-min video recorded**; submission draft complete; stretch: blog post |
| **Aug 31** | Buffer + submit | Re-record window; **submit by 12:00 PDT** (hard rule: never at 4:59) |

## 12. Demo strategy

Video (~4:00): (1) 0:00–0:30 problem — real PDFs + real regex failures; (2) 0:30–1:00 architecture diagram; (3) 1:00–2:45 live unedited single take — Run now → Scout discovers held-back real EOs → queue drains → one EO's reasoning trace → review-queue approve → vehicle search shows exact PN → GCP console (Cloud Run, Scheduler, Vertex graphs); (4) 2:45–3:30 measured results + cost meter; (5) 3:30–4:00 close. Full shot list: `docs/demo-video-script.md`.

## 13. Presentation & submission constraints (2026-08-26)

Constraints layered onto the sections above; where they conflict, this section wins:

1. **Video**: ≤4:00 hard limit, target 3:45. Open with the agent visibly acting on the problem, not setup. Human narration. Centerpiece = the non-trivial workflow: a held-back EO that **supersedes** an existing one, detected and reconciled on camera. Include a run-history scroll showing multiple timestamped scheduled runs — async behavior is evidenced by logs.
2. **Deploy early for evidence**: daily Scheduler enabled Aug 28 and left running → 3+ real scheduled runs in history by recording time. Structured logging is a day-one feature, not polish.
3. **Provenance disclosure**: README discloses pre-existing artifacts — the legacy regex pipeline (pre-window) provides the audit baseline data, the ported deterministic matching logic, and the seeded vehicle reference data; the agentic system is built entirely in-window.
4. **Framing**: lead with autonomous friction-removal; validation gates/guardrails are narrated as architecture. Persistent state is agentic — the registry/diff state drives what fires; review corrections change behavior.
5. **External action**: run-summary email/notification when new EOs are detected (Aug 29, ~1 hr). Firestore surfaced in a console satisfies the core brief; the notification is uplift.
6. **Hosted URL**: the dashboard stays live through the submission window — cheap and differentiating.
7. **Architecture diagram**: one-glance, minimal text, code-accurate.
8. **Submission mechanics**: draft created Aug 30 and updated; final submission by noon Aug 31, never at the deadline.

## 14. Out of scope (explicit)

Writing to the live SmogLegal site/D1; vehicle reference refresh beyond legacy seed (~MY2026); user accounts; Pub/Sub migration; retailer/affiliate features; the corrections-become-few-shot-examples loop (stretch only if ahead of schedule).

## 15. ADK2 graph orchestration (amendment, 2026-08-27 — Lee decision)

The daily-run orchestration moves from the plain-Python `runner.run_once` loop to a real ADK 2 graph (`google.adk.workflow`). Verified against installed google-adk 2.8.0 by a passing local spike.

**Graph** (new `pipeline/workflow_graph.py`): function nodes `scout` → `claim` → (router: item?) → `extract` → `audit` → (router: escalated?) → `match` → loop-edge back to `claim`; `False` route from `claim` → `summarize` (terminal). A no-change day is scout + one empty claim + summarize: zero LLM calls — deterministic routing where no model reasoning is required.

**Verified mechanics used**: `@node` functions with auto-injected `ctx`; parameters bind by name from shared session state; `ctx.state[k]=v` persists via state_delta; `ctx.route = value` selects dict-form edges `(node, {True: a, False: b})`; `Workflow(name=..., edges=[...])` runs as an agent under the standard Runner.

**Extractor as LlmAgent node — timeboxed (90 min)**: attempt a thin `extract_via_agent()` built on an ADK `LlmAgent` with the existing extractor prompt + `Extraction` schema, used by BOTH the graph and `runner.process_work_item` (single extraction implementation is a hard constraint). Acceptance: usage metadata reaches the cost meter, finish_reason (truncation) detection works, gs:// PDF input works, ladder rung-2 (image re-render) still reachable. Any failure → ship fallback: graph-only, extract node calls the existing `extract()` unchanged.

**Durability story (README language)**: the work_items leased queue in Firestore is the pipeline's checkpoint layer; graph sessions are per-invocation and disposable. Recovery = re-trigger; the queue resumes exactly where work stopped.

**Not changing**: backfill worker path, budget guards, audit/match internals, the operator ADK agent (adk_app.py) — it remains the ops surface.
