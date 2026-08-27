import type { APIRoute } from 'astro';
import { callPipeline } from '../../lib/proxy';

// Canonical admin-token pattern for this dashboard: the browser sends the token
// as the X-Admin-Token header on the same-origin /api route, which forwards it
// as-is (never body/query, never logged). A later task (review-action.ts) reuses
// this exact pattern with the same carblegal_admin token.
export const POST: APIRoute = async ({ request }) => {
  const token = request.headers.get('X-Admin-Token') ?? '';

  try {
    return await callPipeline('/admin/run-now', {}, token);
  } catch {
    return new Response(JSON.stringify({ detail: 'pipeline unreachable' }), {
      status: 502,
      headers: { 'Content-Type': 'application/json' },
    });
  }
};
