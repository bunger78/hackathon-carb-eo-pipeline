import type { APIRoute } from 'astro';
import { callPipeline } from '../../lib/proxy';

export const POST: APIRoute = async ({ request }) => {
  let token = '';
  try {
    const body = await request.json();
    if (body && typeof body.token === 'string') token = body.token;
  } catch {
    // no/invalid JSON body — forward an empty token; the pipeline will 401
  }

  try {
    return await callPipeline('/admin/run-now', {}, token);
  } catch {
    return new Response(JSON.stringify({ detail: 'pipeline unreachable' }), {
      status: 502,
      headers: { 'Content-Type': 'application/json' },
    });
  }
};
