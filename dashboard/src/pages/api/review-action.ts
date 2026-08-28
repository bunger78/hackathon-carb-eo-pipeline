import type { APIRoute } from 'astro';
import { callPipeline } from '../../lib/proxy';

// Same canonical admin-token pattern as run-now.ts: the browser sends the
// token as X-Admin-Token on this same-origin route, which forwards it
// unchanged to carb-api's /admin/resolve-review. Pipeline status codes
// (401/404/409/422/503/200) pass straight through via callPipeline so the
// client (ReviewActions.tsx) can render each as a distinct message.
export const POST: APIRoute = async ({ request }) => {
  const token = request.headers.get('X-Admin-Token') ?? '';

  let body: unknown;
  try {
    body = await request.json();
  } catch {
    return new Response(JSON.stringify({ detail: 'invalid JSON body' }), {
      status: 400,
      headers: { 'Content-Type': 'application/json' },
    });
  }

  try {
    return await callPipeline('/admin/resolve-review', body, token);
  } catch {
    return new Response(JSON.stringify({ detail: 'pipeline unreachable' }), {
      status: 502,
      headers: { 'Content-Type': 'application/json' },
    });
  }
};
