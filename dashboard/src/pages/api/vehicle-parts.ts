import type { APIRoute } from 'astro';
import { partsForVehicle } from '../../lib/db';
import type { PartLike } from '../../lib/parts';

// Public read, no auth (see task brief's global constraints) — thin JSON
// wrapper over db.ts's partsForVehicle(). Trims each MatchDoc down to the
// PartLike shape VehiclePicker.tsx (and lib/parts.ts's grouping/sorting)
// expects.
export const GET: APIRoute = async ({ url }) => {
  const vehicleId = url.searchParams.get('vehicle_id') ?? '';
  if (!vehicleId) {
    return new Response(JSON.stringify({ detail: 'vehicle_id query parameter is required' }), {
      status: 400,
      headers: { 'Content-Type': 'application/json' },
    });
  }

  const matches = await partsForVehicle(vehicleId);
  const parts: PartLike[] = matches.map((m) => ({
    eo_number: m.eo_number,
    category: m.category ?? null,
    device_name: m.device_name ?? null,
    manufacturer: m.manufacturer ?? null,
    part_numbers: m.part_numbers ?? [],
    tier: m.tier,
  }));

  return new Response(JSON.stringify(parts), {
    headers: { 'Content-Type': 'application/json' },
  });
};
