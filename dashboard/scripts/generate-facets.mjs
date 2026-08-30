// Generates public/vehicle-facets.json — a static snapshot of the
// year -> make -> model -> trim -> engine cascade that db.ts's
// vehicleCascade() builds from the `vehicles` collection.
//
// `vehicles` is a one-time-seeded reference table (see
// pipeline/seed/seed_vehicles.py), not live pipeline output, so scanning it
// once here at build/generation time (instead of once per Cloud Run cold
// instance, per db.ts's allVehicles()) is safe. Run via `npm run gen:facets`.
//
// Deliberately reimplements the cascade-building logic (mirroring
// src/lib/vehicleCascade.ts + db.ts's vehicleCascade()) in plain JS rather
// than importing the .ts sources, since this is a standalone node script
// with no build step. Keep this in sync with vehicleCascade.ts if that file
// changes.
import { Firestore } from '@google-cloud/firestore';
import { writeFile } from 'node:fs/promises';
import { fileURLToPath } from 'node:url';
import path from 'node:path';

const NUL = String.fromCharCode(0x0); // NO_TRIM sentinel — see vehicleCascade.ts
const UNIT_SEP = String.fromCharCode(0x1); // cascadeKey() separator — see vehicleCascade.ts
const NO_TRIM = NUL;

const INDUCTION_LABELS = { NA: 'N/A', TURBO: 'Turbo', SC: 'Supercharged' };

function cascadeKey(...parts) {
  return parts.join(UNIT_SEP);
}

function engineLabel(v) {
  const parts = [];
  if (v.displacement_l) parts.push(`${v.displacement_l}L`);
  if (v.cylinders) parts.push(`${v.cylinders}-cyl`);
  if (v.induction) parts.push(INDUCTION_LABELS[v.induction] ?? v.induction);
  return parts.length ? parts.join(' ') : 'Unspecified engine';
}

async function main() {
  const db = new Firestore({ projectId: process.env.PROJECT_ID || 'carblegal' });
  const snap = await db.collection('vehicles').get();
  const vehicles = snap.docs.map((d) => ({ id: d.id, ...d.data() }));

  const years = new Set();
  const makesByYear = {};
  const modelsByYearMake = {};
  const trimsByYearMakeModel = {};
  const enginesByYearMakeModelTrim = {};

  for (const v of vehicles) {
    if (typeof v.year !== 'number' || !v.make || !v.model || !v.id) continue;
    const { year, make, model, id } = v;
    const trim = v.trim || NO_TRIM;

    years.add(year);
    (makesByYear[String(year)] ??= new Set()).add(make);
    (modelsByYearMake[cascadeKey(year, make)] ??= new Set()).add(model);
    (trimsByYearMakeModel[cascadeKey(year, make, model)] ??= new Set()).add(trim);
    (enginesByYearMakeModelTrim[cascadeKey(year, make, model, trim)] ??= []).push({
      vehicleId: id,
      label: engineLabel(v),
      displacement_l: v.displacement_l ?? null,
      induction: v.induction ?? null,
      cylinders: v.cylinders ?? null,
    });
  }

  const cascade = {
    years: [...years].sort((a, b) => a - b),
    makesByYear: Object.fromEntries(Object.entries(makesByYear).map(([y, s]) => [y, [...s].sort()])),
    modelsByYearMake: Object.fromEntries(Object.entries(modelsByYearMake).map(([k, s]) => [k, [...s].sort()])),
    trimsByYearMakeModel: Object.fromEntries(Object.entries(trimsByYearMakeModel).map(([k, s]) => [k, [...s].sort()])),
    enginesByYearMakeModelTrim,
  };

  const __dirname = path.dirname(fileURLToPath(import.meta.url));
  const outPath = path.join(__dirname, '..', 'public', 'vehicle-facets.json');
  await writeFile(outPath, JSON.stringify(cascade));
  console.log(`Wrote ${outPath} (${vehicles.length} vehicle docs -> ${cascade.years.length} years)`);
}

main().catch((err) => {
  console.error(err);
  process.exit(1);
});
