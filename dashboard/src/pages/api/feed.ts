import type { APIRoute } from 'astro';
import { latestRunFeed } from '../../lib/db';
import { truncate } from '../../lib/format';

const ERROR_MAX_CHARS = 80;

export const GET: APIRoute = async () => {
  const { run, events } = await latestRunFeed(50);

  const feed = events.map((e) => ({
    ts: typeof e.ts === 'number' ? e.ts : null,
    agent: e.agent ?? '',
    action: e.action ?? '',
    eo: e.eo ?? '',
    // Extras: present only for the actions that write them (see
    // lib/consoleDetail.ts for the full action -> extras mapping); passed
    // through as-is (or omitted) rather than defaulted, so the console can
    // tell "absent" apart from a real falsy value (e.g. gemini_resolved: 0).
    ...(typeof e.ladder_step === 'number' ? { ladder_step: e.ladder_step } : {}),
    ...(typeof e.confidence === 'number' ? { confidence: e.confidence } : {}),
    ...(typeof e.count === 'number' ? { count: e.count } : {}),
    ...(typeof e.gemini_resolved === 'number' ? { gemini_resolved: e.gemini_resolved } : {}),
    ...(typeof e.reason === 'string' ? { reason: e.reason } : {}),
    ...(typeof e.error === 'string' ? { error: truncate(e.error, ERROR_MAX_CHARS) } : {}),
  }));

  const runInfo = run && {
    trigger: run.trigger ?? null,
    status: run.status ?? null,
    cost_usd: typeof run.cost_usd === 'number' ? run.cost_usd : null,
    tok_in: typeof run.tok_in === 'number' ? run.tok_in : null,
    tok_out: typeof run.tok_out === 'number' ? run.tok_out : null,
    started_at: typeof run.started_at === 'number' ? run.started_at : null,
    healed: typeof run.healed === 'number' ? run.healed : null,
  };

  return new Response(JSON.stringify({ run: runInfo ?? null, events: feed }), {
    headers: { 'Content-Type': 'application/json' },
  });
};
