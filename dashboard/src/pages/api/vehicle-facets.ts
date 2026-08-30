import type { APIRoute } from 'astro';
import { vehicleCascade } from '../../lib/db';

// Superseded by the static /vehicle-facets.json (see scripts/generate-facets.mjs
// and VehiclePicker.tsx) — kept here as a fallback, not called by the dashboard
// itself. Public read, no auth (see task brief's global constraints) — thin
// JSON wrapper over db.ts's vehicleCascade(), which memoizes the full
// `vehicles` scan in module memory after the first request. `vehicles` is a
// one-time-seeded reference table (see db.ts's allVehicles()), not live
// pipeline output, so an hour of client/CDN caching is safe — unlike
// vehicle-parts.ts, which reads live `matches` data and must NOT be cached.
export const GET: APIRoute = async () => {
  const cascade = await vehicleCascade();
  return new Response(JSON.stringify(cascade), {
    headers: { 'Content-Type': 'application/json', 'Cache-Control': 'public, max-age=3600' },
  });
};
