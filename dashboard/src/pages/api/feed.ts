import type { APIRoute } from 'astro';
import { latestRunFeed } from '../../lib/db';

export const GET: APIRoute = async () => {
  const { events } = await latestRunFeed(50);
  const feed = events.map((e) => ({
    ts: typeof e.ts === 'number' ? e.ts : null,
    agent: e.agent ?? '',
    action: e.action ?? '',
    eo: e.eo ?? '',
  }));
  return new Response(JSON.stringify(feed), {
    headers: { 'Content-Type': 'application/json' },
  });
};
