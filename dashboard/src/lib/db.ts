// Firestore singleton + typed readers. ALL queries live here.
//
// Read-only surface over the carb-eo-pipeline's Firestore data (see
// pipeline/core/db.py for the writer-side contract). Every reader tolerates
// empty/missing collections and documents — the backfill may be mid-flight.
//
// IMPORTANT: `extractions/{eo}_v{n}` docs are an ENVELOPE — the Extraction
// fields live nested under `.payload`. Every reader below unwraps `payload`
// before handing data to callers; callers (and lib/diff.ts) never see the
// raw envelope shape.
import { AggregateField, FieldPath, Firestore, type Query } from '@google-cloud/firestore';
import { prefixRange } from './prefixRange';
import { cascadeKey, engineLabel, NO_TRIM, type VehicleCascade, type VehicleEngineOption } from './vehicleCascade';

const db = new Firestore({ projectId: process.env.PROJECT_ID || 'carblegal' });

// --- shared doc shapes (subset of fields this dashboard reads) ---

export interface EoDoc {
  state?: string;
  pdf_url?: string;
  gcs_uri?: string;
  supersedes?: string[];
  superseded_by?: string;
  match_count?: number;
  first_seen?: number;
  device_name?: string;
  manufacturer?: string;
  category?: string;
  part_numbers?: string[];
  confidence?: number;
}

export interface FitmentRow {
  part_numbers: string[];
  year_start?: number | null;
  year_end?: number | null;
  make?: string | null;
  model?: string | null;
  trim_note?: string | null;
  displacement_l?: number | null;
  induction?: 'NA' | 'TURBO' | 'SC' | null;
  cylinders?: number | null;
}

export interface ExtractionPayload {
  eo_number: string;
  issue_date?: string | null;
  manufacturer?: string | null;
  device_name?: string | null;
  category?: string | null;
  description?: string | null;
  supersedes: string[];
  part_numbers: string[];
  fitment: FitmentRow[];
  confidence: number;
  sections_confidence?: Record<string, number>;
  illegible_pages?: number[];
  notes?: string | null;
}

interface ExtractionEnvelope {
  eo_number: string;
  payload: ExtractionPayload;
  prompt_version?: string;
  ladder_step?: number;
  finish_reason?: string;
  tok_in?: number;
  tok_out?: number;
  cost_usd?: number;
  created_at?: number;
}

export interface ExtractionSummary {
  version: number;
  promptVersion?: string;
  ladderStep?: number;
  finishReason?: string;
  tokIn?: number;
  tokOut?: number;
  costUsd?: number;
  createdAt?: number;
  payload: ExtractionPayload;
}

export interface LegacyDoc {
  device_name?: string;
  manufacturer?: string;
  category?: string;
  part_numbers?: string[];
  fitment_count?: number;
}

export interface MatchDoc {
  vehicle_id: string;
  eo_number: string;
  tier: 'exact' | 'high' | 'medium' | 'generic';
  method: 'deterministic' | 'gemini_resolved';
  part_numbers: string[];
  category?: string | null;
  device_name?: string | null;
  manufacturer?: string | null;
  rationale?: string;
}

export interface VehicleDoc {
  id?: string;
  year?: number;
  make?: string;
  model?: string;
  trim?: string;
  displacement_l?: number | null;
  induction?: 'NA' | 'TURBO' | 'SC' | null;
  cylinders?: number | null;
}

export interface ReviewDoc {
  id?: string;
  status: 'open' | 'approved' | 'rejected';
  eo_number: string;
  reason: string;
  agent_notes?: string;
  payload?: unknown;
  created_at?: number;
  resolved_at?: number;
}

export interface RunDoc {
  id?: string;
  trigger?: string;
  started_at?: number;
  finished_at?: number;
  status?: string;
  cost_usd?: number;
  tok_in?: number;
  tok_out?: number;
  // Merged into the doc by finish_run() (pipeline/core/db.py) once the run
  // completes — see workflow_graph.py's summarize() node. Absent while running.
  new_eos?: number;
  completed?: number;
  needs_review?: number;
  failed?: number;
  // Count of transient-failure work items auto-requeued by the heal stage
  // (pipeline/agents/healer.py's requeue_transient_failures()). Absent while running.
  healed?: number;
}

export interface RunEvent {
  id?: string;
  ts?: number;
  agent?: string;
  action?: string;
  eo?: string;
  [key: string]: unknown;
}

// --- overview ---

// The pipeline writes `state` to one of these values (see pipeline/core/db.py
// and agents/*.py, including agents/auditor.py's superseded transition).
const EO_STATES = ['discovered', 'matching', 'needs_review', 'complete', 'failed', 'superseded'] as const;

export async function overviewCounts(): Promise<{ states: Record<string, number>; total: number }> {
  const counts: Record<string, number> = {};
  await Promise.all(
    EO_STATES.map(async (state) => {
      const snap = await db.collection('eos').where('state', '==', state).count().get();
      counts[state] = snap.data().count;
    })
  );
  const total = Object.values(counts).reduce((a, b) => a + b, 0);
  return { states: counts, total };
}

// Total Gemini spend across only backfill + scheduled (daily) runs — the honest
// input to the overview receipt stat's on-camera cost claim. Deliberately
// excludes "manual" (the Run Now button), "adk", and "review-resolve" triggers
// (see pipeline/main.py, backfill.py, adk_app.py, agents/reviewer.py), which
// would otherwise inflate the number every time an operator clicks a button.
// Two queries (equality on "scheduled" + a prefix range on "backfill"/
// "backfill-worker-{n}") rather than one, since Firestore can't OR an equality
// and a range clause together server-side; each needs no composite index. The
// prefix bound itself is built by prefixRange() (lib/prefixRange.ts), which is
// unit-tested in isolation — see prefixRange.test.ts.
export async function pipelineSpend(): Promise<number> {
  const backfillRange = prefixRange('backfill');
  const [scheduledSnap, backfillSnap] = await Promise.all([
    db.collection('runs').where('trigger', '==', 'scheduled').aggregate({ total: AggregateField.sum('cost_usd') }).get(),
    db
      .collection('runs')
      .where('trigger', '>=', backfillRange.gte)
      .where('trigger', '<', backfillRange.lt)
      .aggregate({ total: AggregateField.sum('cost_usd') })
      .get(),
  ]);
  return (scheduledSnap.data().total ?? 0) + (backfillSnap.data().total ?? 0);
}

// Count of extraction docs (`extractions/{eo}_v{n}`) — each doc is one extraction
// attempt, so this doubles as "extractions performed" for the overview receipt stat.
export async function extractionsCount(): Promise<number> {
  const snap = await db.collection('extractions').count().get();
  return snap.data().count;
}

// --- runs ---

export async function recentRuns(n: number): Promise<RunDoc[]> {
  const snap = await db.collection('runs').orderBy('started_at', 'desc').limit(n).get();
  return snap.docs.map((d) => ({ id: d.id, ...(d.data() as RunDoc) }));
}

// Cap on events read/rendered for a single run's detail page — a backfill run
// touching many EOs (4 stage events each) can produce thousands of docs, and
// this is the only reader in this module that was unbounded (see recentRuns/
// latestRunFeed/openReviews/vehiclesMatching/searchEos, all capped). The page
// layer shows a "showing first N" disclosure when the cap is hit.
export const RUN_EVENTS_READ_LIMIT = 500;

export async function runWithEvents(id: string): Promise<{ run: RunDoc; events: RunEvent[] } | null> {
  const runSnap = await db.collection('runs').doc(id).get();
  if (!runSnap.exists) return null;
  // orderBy('ts') silently excludes any event doc missing `ts` entirely (Firestore
  // semantics) — accepted, since every writer here always sets it.
  const eventsSnap = await db
    .collection('runs')
    .doc(id)
    .collection('events')
    .orderBy('ts', 'asc')
    .limit(RUN_EVENTS_READ_LIMIT)
    .get();
  return {
    run: { id: runSnap.id, ...(runSnap.data() as RunDoc) },
    events: eventsSnap.docs.map((d) => ({ id: d.id, ...(d.data() as RunEvent) })),
  };
}

// For the live agent console: the latest run's own doc (trigger/status/cost/
// tokens/healed for the header strip) plus its most recent events, newest
// first, capped at `limit` — cheaper than runWithEvents() for a 3s poll loop
// since it never fetches a run's full event history.
export async function latestRunFeed(limit: number): Promise<{ runId: string | null; run: RunDoc | null; events: RunEvent[] }> {
  const runsSnap = await db.collection('runs').orderBy('started_at', 'desc').limit(1).get();
  if (runsSnap.empty) return { runId: null, run: null, events: [] };
  const runDoc = runsSnap.docs[0];
  const eventsSnap = await runDoc.ref.collection('events').orderBy('ts', 'desc').limit(limit).get();
  return {
    runId: runDoc.id,
    run: { id: runDoc.id, ...(runDoc.data() as RunDoc) },
    events: eventsSnap.docs.map((d) => ({ id: d.id, ...(d.data() as RunEvent) })),
  };
}

// --- eos / search ---

export type EoSearchBy = 'eo' | 'part' | 'manufacturer';

export interface EoSearchFilters {
  by?: EoSearchBy;
  /** Exact `state` equality filter (see EO_STATES). Ignored in 'part' mode — `matches` docs don't carry the parent eo's state, and joining would cost one extra read per grouped result. */
  status?: string;
}

export interface EoSummary {
  eo: string;
  state?: string;
  manufacturer?: string;
  device_name?: string;
  match_count?: number;
}

export interface EoSearchResult {
  results: EoSummary[];
  nextCursor: string | null;
  /** true if the part-number search hit PART_SEARCH_FETCH_CAP or PART_SEARCH_RESULT_CAP and more matches may exist. Always false for 'eo'/'manufacturer' modes (those paginate instead). */
  truncated?: boolean;
}

// The EO browser page (pages/eos/index.astro) pages 'eo'/'manufacturer' results
// at this size; exported so the page can tell "last page" (a short page) apart
// from "maybe more" (a full page) without duplicating the constant.
export const SEARCH_PAGE_SIZE = 50;

// Part-number search has no natural page size (a single popular part number can
// match hundreds of vehicles under one EO), so instead of cursor-paginating raw
// `matches` docs (which could burn many round-trips returning ~1 new EO per page),
// we scan a bounded number of match docs once, group by eo_number in JS, and cap
// the number of distinct EOs returned. `truncated` tells the caller more may exist.
const PART_SEARCH_FETCH_CAP = 200; // max raw `matches` docs scanned
const PART_SEARCH_RESULT_CAP = 50; // max distinct EOs returned

function summarizeEo(id: string, data: EoDoc): EoSummary {
  return { eo: id, state: data.state, manufacturer: data.manufacturer, device_name: data.device_name, match_count: data.match_count };
}

export async function searchEos(q: string, filters: EoSearchFilters = {}, cursor?: string): Promise<EoSearchResult> {
  const by = filters.by ?? 'eo';
  const query = (q ?? '').trim();
  const status = filters.status;

  if (by === 'part') {
    if (!query) return { results: [], nextCursor: null };
    // No orderBy here: array-contains alone needs no composite index. Grouping
    // and the result cap are handled here in JS instead of via Firestore-side
    // cursor pagination (see PART_SEARCH_FETCH_CAP/PART_SEARCH_RESULT_CAP above).
    // `status` is not applied here — `matches` docs do not carry the parent
    // eo's state, and joining would cost one extra read per grouped result.
    const snap = await db
      .collection('matches')
      .where('part_numbers', 'array-contains', query)
      .limit(PART_SEARCH_FETCH_CAP)
      .get();
    const seen = new Map<string, EoSummary>();
    for (const d of snap.docs) {
      const m = d.data() as MatchDoc;
      if (!seen.has(m.eo_number)) {
        seen.set(m.eo_number, { eo: m.eo_number, manufacturer: m.manufacturer ?? undefined, device_name: m.device_name ?? undefined });
      }
    }
    const grouped = [...seen.values()];
    const results = grouped.slice(0, PART_SEARCH_RESULT_CAP);
    const truncated = grouped.length > PART_SEARCH_RESULT_CAP || snap.docs.length >= PART_SEARCH_FETCH_CAP;
    return { results, nextCursor: null, truncated };
  }

  if (by === 'manufacturer') {
    if (!query) return { results: [], nextCursor: null };
    // Equality filter(s) + orderBy(documentId()) need no composite index —
    // Firestore always maintains an index on document name and can combine it
    // with any number of equality clauses for free.
    let ref: Query = db.collection('eos').where('manufacturer', '==', query);
    if (status) ref = ref.where('state', '==', status);
    ref = ref.orderBy(FieldPath.documentId());
    if (cursor) ref = ref.startAfter(cursor);
    ref = ref.limit(SEARCH_PAGE_SIZE);
    const snap = await ref.get();
    const results = snap.docs.map((d) => summarizeEo(d.id, d.data() as EoDoc));
    const last = snap.docs.at(-1);
    return { results, nextCursor: last ? last.id : null };
  }

  // by === 'eo': prefix range query on the document id when `query` is given
  // (via prefixRange() — U+F8FF, end of the Unicode private-use area, bounds
  // "starts with query"); with an empty query this instead browses the full
  // collection ordered by id, which backs the EO browser's default
  // (no-search-term) listing. A cursor is itself a startAfter bound, so it
  // cannot be combined with startAt — only one start-cursor is used per call.
  let ref: Query = db.collection('eos');
  if (status) ref = ref.where('state', '==', status);
  ref = ref.orderBy(FieldPath.documentId());
  if (query) {
    const upperBound = prefixRange(query).lt;
    ref = cursor ? ref.startAfter(cursor).endAt(upperBound) : ref.startAt(query).endAt(upperBound);
  } else if (cursor) {
    ref = ref.startAfter(cursor);
  }
  ref = ref.limit(SEARCH_PAGE_SIZE);
  const snap = await ref.get();
  const results = snap.docs.map((d) => summarizeEo(d.id, d.data() as EoDoc));
  const last = snap.docs.at(-1);
  return { results, nextCursor: last ? last.id : null };
}

function parseVersion(docId: string): number {
  const m = /_v(\d+)$/.exec(docId);
  return m ? Number(m[1]) : 1;
}

export interface LineageNode {
  eo: string;
  exists: boolean;
  state?: string;
}

async function fetchEo(cache: Map<string, EoDoc | null>, eo: string): Promise<EoDoc | null> {
  if (cache.has(eo)) return cache.get(eo)!;
  const snap = await db.collection('eos').doc(eo).get();
  const data = snap.exists ? (snap.data() as EoDoc) : null;
  cache.set(eo, data);
  return data;
}

interface LineageWalk {
  nodes: LineageNode[];
  /** true if the walk hit maxDepth while the outermost layer still had unexplored supersedes/superseded_by links — i.e. the chain may continue beyond what's returned. False if it stopped because the chain simply ran out. */
  capped: boolean;
}

async function walkLineage(
  cache: Map<string, EoDoc | null>,
  startEo: string,
  direction: 'back' | 'forward',
  maxDepth = 5
): Promise<LineageWalk> {
  const visited = new Set<string>([startEo]);
  const result: LineageNode[] = [];
  let frontier = [startEo];
  for (let depth = 0; depth < maxDepth && frontier.length; depth++) {
    const next: string[] = [];
    for (const eo of frontier) {
      const data = await fetchEo(cache, eo);
      const neighbors = direction === 'back' ? data?.supersedes ?? [] : data?.superseded_by ? [data.superseded_by] : [];
      for (const n of neighbors) {
        if (visited.has(n)) continue;
        visited.add(n);
        next.push(n);
      }
    }
    for (const eo of next) {
      const data = await fetchEo(cache, eo);
      result.push({ eo, exists: data !== null, state: data?.state });
    }
    frontier = next;
  }
  // `frontier` still holds the last layer found: if non-empty, the loop exited
  // on the maxDepth condition with that layer's own neighbors never checked
  // (the depth cap was hit, not a naturally-ended chain).
  return { nodes: result, capped: frontier.length > 0 };
}

export interface EoDetail {
  eo: string;
  exists: boolean;
  data: EoDoc | null;
  latestExtraction: ExtractionSummary | null;
  lineage: { back: LineageNode[]; forward: LineageNode[]; backCapped: boolean; forwardCapped: boolean };
}

export async function eoDetail(eo: string): Promise<EoDetail> {
  const eoSnap = await db.collection('eos').doc(eo).get();
  const eoData = eoSnap.exists ? (eoSnap.data() as EoDoc) : null;

  // NOTE: the brief describes ordering by an extraction "version" field, but
  // pipeline/agents/extractor.py never writes a `version` field — the version
  // number only exists embedded in the doc id (`{eo}_v{n}`). `created_at` is
  // monotonic with version (next_extraction_version counts existing docs), so
  // it's used here as the equivalent "latest" ordering instead of a full
  // fetch-all-and-sort.
  const extractionSnap = await db
    .collection('extractions')
    .where('eo_number', '==', eo)
    .orderBy('created_at', 'desc')
    .limit(1)
    .get();

  let latestExtraction: ExtractionSummary | null = null;
  if (!extractionSnap.empty) {
    const doc = extractionSnap.docs[0];
    const data = doc.data() as ExtractionEnvelope;
    latestExtraction = {
      version: parseVersion(doc.id),
      promptVersion: data.prompt_version,
      ladderStep: data.ladder_step,
      finishReason: data.finish_reason,
      tokIn: data.tok_in,
      tokOut: data.tok_out,
      costUsd: data.cost_usd,
      createdAt: data.created_at,
      payload: data.payload,
    };
  }

  // Lineage: follow supersedes[]/superseded_by both directions, max depth 5.
  // (Last-20-events-across-runs is intentionally NOT included here — that needs
  // a collection-group index over per-run `events` subcollections and is
  // expensive; show a link to the latest run instead, at the page layer.)
  const cache = new Map<string, EoDoc | null>([[eo, eoData]]);
  const [back, forward] = await Promise.all([walkLineage(cache, eo, 'back'), walkLineage(cache, eo, 'forward')]);

  return {
    eo,
    exists: eoSnap.exists,
    data: eoData,
    latestExtraction,
    lineage: { back: back.nodes, forward: forward.nodes, backCapped: back.capped, forwardCapped: forward.capped },
  };
}

export async function legacyFor(eo: string): Promise<LegacyDoc | null> {
  const snap = await db.collection('legacy_extractions').doc(eo).get();
  return snap.exists ? (snap.data() as LegacyDoc) : null;
}

// --- work items (EO detail "why it failed" panel) ---

export interface FailureInfo {
  lastError: string;
  attempts: number;
  stage: string;
}

// Newest work_items doc for this EO (there can be more than one across runs —
// see pipeline/core/db.py's Repo.latest_work_item, which the retry endpoint
// uses server-side), ordered by created_at descending so a stale duplicate is
// never mistaken for the current one — same "latest" idiom eoDetail() above
// uses for the newest extraction.
export async function failureInfo(eo: string): Promise<FailureInfo | null> {
  const snap = await db.collection('work_items').where('eo_number', '==', eo).orderBy('created_at', 'desc').limit(1).get();
  if (snap.empty) return null;
  const data = snap.docs[0].data() as { last_error?: string; attempts?: number; stage?: string };
  return {
    lastError: data.last_error ?? '',
    attempts: data.attempts ?? 0,
    stage: data.stage ?? 'unknown',
  };
}

// --- review queue ---

export async function openReviews(): Promise<ReviewDoc[]> {
  const snap = await db.collection('review_queue').where('status', '==', 'open').orderBy('created_at', 'asc').limit(50).get();
  return snap.docs.map((d) => ({ id: d.id, ...(d.data() as ReviewDoc) }));
}

// True count of open reviews (openReviews() above caps at 50 docs fetched, which
// would undercount during a busy backfill).
export async function openReviewCount(): Promise<number> {
  const snap = await db.collection('review_queue').where('status', '==', 'open').count().get();
  return snap.data().count;
}

export async function reviewDetail(id: string): Promise<ReviewDoc | null> {
  const snap = await db.collection('review_queue').doc(id).get();
  return snap.exists ? { id: snap.id, ...(snap.data() as ReviewDoc) } : null;
}

// Collapsed "resolved" section on the review queue index. Deliberately no
// `orderBy` alongside the `in` filter — an `in` + orderBy-on-a-different-field
// query needs a composite index (see overviewCounts/pipelineSpend/
// vehiclesMatching above for this file's running effort to avoid requiring
// any), so instead this fetches a bounded, unsorted batch and sorts/caps in JS,
// the same tactic searchEos() uses for part-number search.
const RESOLVED_REVIEWS_FETCH_LIMIT = 100;
const RESOLVED_REVIEWS_RESULT_LIMIT = 20;

export async function resolvedReviews(): Promise<ReviewDoc[]> {
  const snap = await db
    .collection('review_queue')
    .where('status', 'in', ['approved', 'rejected'])
    .limit(RESOLVED_REVIEWS_FETCH_LIMIT)
    .get();
  const docs = snap.docs.map((d) => ({ id: d.id, ...(d.data() as ReviewDoc) }));
  docs.sort((a, b) => (b.resolved_at ?? 0) - (a.resolved_at ?? 0));
  return docs.slice(0, RESOLVED_REVIEWS_RESULT_LIMIT);
}

// Single-field lookup for the review-detail page's PDF link — avoids the
// heavier eoDetail() (which also fetches the latest extraction and walks
// supersession lineage) when only pdf_url is needed.
export async function eoPdfUrl(eo: string): Promise<string | null> {
  const snap = await db.collection('eos').doc(eo).get();
  if (!snap.exists) return null;
  return (snap.data() as EoDoc).pdf_url ?? null;
}

// --- vehicles ---

export interface VehicleFacets {
  makes: string[];
  years: number[];
  modelsByMake: Record<string, string[]>;
  inductions: string[];
}

// The `vehicles` collection is a bounded, one-time-seeded reference table (see
// pipeline/seed/seed_vehicles.py) rather than a continuously-growing collection,
// so it's cheap and safe to cache the whole thing in memory for the process
// lifetime. Module-scope memoized promise: the first caller triggers the scan,
// concurrent callers await the same in-flight promise, and the result is reused
// for every subsequent call (no TTL — see Base.astro/deploy notes for restarts).
// Both vehicleFacets() and vehiclesMatching() share this cache, which also lets
// vehiclesMatching() filter/sort entirely in JS instead of needing a composite
// Firestore index for every make/model/induction/year combination.
let vehiclesCache: Promise<VehicleDoc[]> | null = null;

async function allVehicles(): Promise<VehicleDoc[]> {
  if (!vehiclesCache) {
    vehiclesCache = db
      .collection('vehicles')
      .get()
      .then((snap) => snap.docs.map((d) => ({ id: d.id, ...(d.data() as VehicleDoc) })))
      .catch((err) => {
        vehiclesCache = null; // don't cache a failure forever; let the next call retry
        throw err;
      });
  }
  return vehiclesCache;
}

export async function vehicleFacets(): Promise<VehicleFacets> {
  const vehicles = await allVehicles();
  const makes = new Set<string>();
  const years = new Set<number>();
  const inductions = new Set<string>();
  const modelsByMake: Record<string, Set<string>> = {};
  for (const v of vehicles) {
    if (v.make) {
      makes.add(v.make);
      (modelsByMake[v.make] ??= new Set<string>());
      if (v.model) modelsByMake[v.make].add(v.model);
    }
    if (typeof v.year === 'number') years.add(v.year);
    if (v.induction) inductions.add(v.induction);
  }
  return {
    makes: [...makes].sort(),
    years: [...years].sort((a, b) => a - b),
    modelsByMake: Object.fromEntries(Object.entries(modelsByMake).map(([k, s]) => [k, [...s].sort()])),
    inductions: [...inductions].sort(),
  };
}

export interface VehicleFilter {
  make?: string;
  model?: string;
  year?: number;
  yearMin?: number;
  yearMax?: number;
  induction?: string;
}

const VEHICLES_MATCH_LIMIT = 200;

// Filters the cached full vehicle list in JS rather than issuing a Firestore
// query: `facets` allows any combination of make/model/induction equality plus
// an exact year or a year range, and a Firestore composite index would be
// needed for each distinct combination of equality fields + the year
// range/orderBy (make+year, make+model+year, model+induction+year, ...). Since
// `allVehicles()` is already cached (see vehicleFacets above), filtering here
// costs no extra Firestore reads and needs no additional indexes.
export async function vehiclesMatching(facets: VehicleFilter = {}): Promise<VehicleDoc[]> {
  const vehicles = await allVehicles();
  const filtered = vehicles.filter((v) => {
    if (facets.make && v.make !== facets.make) return false;
    if (facets.model && v.model !== facets.model) return false;
    if (facets.induction && v.induction !== facets.induction) return false;
    if (typeof facets.year === 'number') {
      if (v.year !== facets.year) return false;
    } else {
      if (typeof facets.yearMin === 'number' && (v.year ?? -Infinity) < facets.yearMin) return false;
      if (typeof facets.yearMax === 'number' && (v.year ?? Infinity) > facets.yearMax) return false;
    }
    return true;
  });
  filtered.sort((a, b) => (a.year ?? 0) - (b.year ?? 0));
  return filtered.slice(0, VEHICLES_MATCH_LIMIT);
}

export async function partsForVehicle(vehicleId: string, category?: string): Promise<MatchDoc[]> {
  let ref: Query = db.collection('matches').where('vehicle_id', '==', vehicleId);
  if (category) ref = ref.where('category', '==', category);
  const snap = await ref.get();
  return snap.docs.map((d) => ({ id: d.id, ...(d.data() as MatchDoc) }));
}

// --- vehicle search cascade (D7) ---
//
// The brief sketched a facets shape of {years, makesByYear, modelsByYearMake,
// trims, engines}; this builds that same year -> make -> model -> trim ->
// engine cascade over the same memoized allVehicles() cache vehicleFacets()/
// vehiclesMatching() share above (so no extra Firestore reads), using the
// actual VehicleDoc fields — there's no separate "engine" field in the
// vehicles collection, so "engine" here means the (displacement_l,
// induction, cylinders) triple carried directly on each doc (see
// pipeline/seed/seed_vehicles.py). Cascade key-building (cascadeKey/NO_TRIM/
// engineLabel) lives in lib/vehicleCascade.ts, which has no Firestore import,
// so VehiclePicker.tsx (the browser bundle) can reuse the exact same key
// logic to read this function's JSON response without pulling in
// @google-cloud/firestore client-side.
export async function vehicleCascade(): Promise<VehicleCascade> {
  const vehicles = await allVehicles();
  const years = new Set<number>();
  const makesByYear: Record<string, Set<string>> = {};
  const modelsByYearMake: Record<string, Set<string>> = {};
  const trimsByYearMakeModel: Record<string, Set<string>> = {};
  const enginesByYearMakeModelTrim: Record<string, VehicleEngineOption[]> = {};

  for (const v of vehicles) {
    if (typeof v.year !== 'number' || !v.make || !v.model || !v.id) continue;
    const { year, make, model, id } = v;
    const trim = v.trim || NO_TRIM;

    years.add(year);
    (makesByYear[String(year)] ??= new Set<string>()).add(make);
    (modelsByYearMake[cascadeKey(year, make)] ??= new Set<string>()).add(model);
    (trimsByYearMakeModel[cascadeKey(year, make, model)] ??= new Set<string>()).add(trim);
    (enginesByYearMakeModelTrim[cascadeKey(year, make, model, trim)] ??= []).push({
      vehicleId: id,
      label: engineLabel(v),
      displacement_l: v.displacement_l ?? null,
      induction: v.induction ?? null,
      cylinders: v.cylinders ?? null,
    });
  }

  return {
    years: [...years].sort((a, b) => a - b),
    makesByYear: Object.fromEntries(Object.entries(makesByYear).map(([y, s]) => [y, [...s].sort()])),
    modelsByYearMake: Object.fromEntries(Object.entries(modelsByYearMake).map(([k, s]) => [k, [...s].sort()])),
    trimsByYearMakeModel: Object.fromEntries(Object.entries(trimsByYearMakeModel).map(([k, s]) => [k, [...s].sort()])),
    enginesByYearMakeModelTrim,
  };
}
