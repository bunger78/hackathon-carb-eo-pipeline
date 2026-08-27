// Composed stats for the overview page's tiles + receipt stat. Wraps several
// db.ts aggregate reads behind a single 60s module-scope cache so the SSR page
// render and the api/overview.ts JSON endpoint don't double up on Firestore
// aggregate queries (see task-d3-brief.md: "compute server-side, cache 60s").
import { extractionsCount, openReviewCount, overviewCounts, pipelineSpend, recentRuns, type RunDoc } from './db';

export interface OverviewData {
  states: Record<string, number>;
  totalEos: number;
  extractionsCount: number;
  totalCostUsd: number;
  recentRuns: RunDoc[];
  nextScheduled: string;
  openReviewCount: number;
}

const CACHE_TTL_MS = 60_000;
const RECENT_RUNS_LIMIT = 5;

let cached: { data: OverviewData; expiresAt: number } | null = null;

export async function getOverviewData(): Promise<OverviewData> {
  if (cached && cached.expiresAt > Date.now()) return cached.data;

  const [counts, totalCostUsd, extractions, runs, reviewCount] = await Promise.all([
    overviewCounts(),
    pipelineSpend(),
    extractionsCount(),
    recentRuns(RECENT_RUNS_LIMIT),
    openReviewCount(),
  ]);

  const data: OverviewData = {
    states: counts.states,
    totalEos: counts.total,
    extractionsCount: extractions,
    totalCostUsd,
    recentRuns: runs,
    nextScheduled: 'Daily 6:00 AM PT',
    openReviewCount: reviewCount,
  };

  cached = { data, expiresAt: Date.now() + CACHE_TTL_MS };
  return data;
}
