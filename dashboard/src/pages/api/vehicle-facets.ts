import type { APIRoute } from 'astro';
import { vehicleCascade } from '../../lib/db';

// Public read, no auth (see task brief's global constraints) — thin JSON
// wrapper over db.ts's vehicleCascade(), which memoizes the full `vehicles`
// scan in module memory after the first request.
export const GET: APIRoute = async () => {
  const cascade = await vehicleCascade();
  return new Response(JSON.stringify(cascade), {
    headers: { 'Content-Type': 'application/json' },
  });
};
