import { gzipSync } from 'node:zlib';
import { readFileSync } from 'node:fs';
import { fileURLToPath } from 'node:url';
import type { APIRoute } from 'astro';
import { vehicleCascade } from '../../lib/db';

// Serves the pre-generated facets (public/vehicle-facets.json) PRE-GZIPPED from
// memory: the node adapter doesn't compress static assets, so fetching the raw
// file cost a ~4.8MB uncompressed download (~7s). Gzipped once at module init
// it's ~0.5MB on the wire. Falls back to the live Firestore cascade if the
// generated file is missing (fresh clone before `npm run gen:facets`).
// `vehicles` is a one-time-seeded reference table, so an hour of client caching
// is safe — unlike vehicle-parts.ts, which reads live `matches` data.
let gzipped: Buffer | null = null;
try {
  const raw = readFileSync(
    fileURLToPath(new URL('../../../public/vehicle-facets.json', import.meta.url)));
  gzipped = gzipSync(raw, { level: 9 });
} catch {
  gzipped = null; // fall back to live cascade per-request
}

export const GET: APIRoute = async () => {
  if (gzipped) {
    return new Response(new Uint8Array(gzipped), {
      headers: {
        'Content-Type': 'application/json',
        'Content-Encoding': 'gzip',
        'Cache-Control': 'public, max-age=3600',
        Vary: 'Accept-Encoding',
      },
    });
  }
  const cascade = await vehicleCascade();
  return new Response(JSON.stringify(cascade), {
    headers: { 'Content-Type': 'application/json', 'Cache-Control': 'public, max-age=3600' },
  });
};
