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

// For the live agent console: only the latest run's most recent events, newest
// first, capped at `limit` — cheaper than runWithEvents() for a 3s poll loop
// since it never fetches a run's full event history.
export async function latestRunFeed(limit: number): Promise<{ runId: string | null; events: RunEvent[] }> {
  const runsSnap = await db.collection('runs').orderBy('started_at', 'desc').limit(1).get();
  if (runsSnap.empty) return { runId: null, events: [] };
  const runDoc = runsSnap.docs[0];
  const eventsSnap = await runDoc.ref.collection('events').orderBy('ts', 'desc').limit(limit).get();
  return {
    runId: runDoc.id,
    events: eventsSnap.docs.map((d) => ({ id: d.id, ...(d.data() as RunEvent) })),
  };
}

// --- eos / search ---

export type EoSearchBy = 'eo' | 'part' | 'manufacturer';

export interface EoSearchFilters {
  by?: EoSearchBy;
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

const SEARCH_PAGE_SIZE = 25;

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
  if (!query) return { results: [], nextCursor: null };

  if (by === 'part') {
    // No orderBy here: array-contains alone needs no composite index. Grouping
    // and the result cap are handled here in JS instead of via Firestore-side
    // cursor pagination (see PART_SEARCH_FETCH_CAP/PART_SEARCH_RESULT_CAP above).
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
    let ref: Query = db.collection('eos').where('manufacturer', '==', query).orderBy(FieldPath.documentId());
    if (cursor) ref = ref.startAfter(cursor);
    ref = ref.limit(SEARCH_PAGE_SIZE);
    const snap = await ref.get();
    const results = snap.docs.map((d) => summarizeEo(d.id, d.data() as EoDoc));
    const last = snap.docs.at(-1);
    return { results, nextCursor: last ? last.id : null };
  }

  // by === 'eo': prefix range query on the document id. U+F8FF is a very high
  // code point (end of the Unicode private-use area), so [query, query+U+F8FF]
  // bounds "starts with query". A cursor is itself a startAfter bound, so it
  // cannot be combined with startAt — only one start-cursor is used per call.
  const upperBound = query + '';
  let ref: Query = db.collection('eos').orderBy(FieldPath.documentId());
  ref = cursor ? ref.startAfter(cursor).endAt(upperBound) : ref.startAt(query).endAt(upperBound);
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

async function walkLineage(
  cache: Map<string, EoDoc | null>,
  startEo: string,
  direction: 'back' | 'forward',
  maxDepth = 5
): Promise<LineageNode[]> {
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
  return result;
}

export interface EoDetail {
  eo: string;
  exists: boolean;
  data: EoDoc | null;
  latestExtraction: ExtractionSummary | null;
  lineage: { back: LineageNode[]; forward: LineageNode[] };
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

  return { eo, exists: eoSnap.exists, data: eoData, latestExtraction, lineage: { back, forward } };
}

export async function legacyFor(eo: string): Promise<LegacyDoc | null> {
  const snap = await db.collection('legacy_extractions').doc(eo).get();
  return snap.exists ? (snap.data() as LegacyDoc) : null;
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
