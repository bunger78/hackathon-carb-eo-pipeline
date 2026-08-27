// Pure legacy-vs-agent extraction diff. No I/O, no Firestore.
//
// `legacy` is a `legacy_extractions/{eo}` doc (flat fields).
// `agent` is an unwrapped `Extraction` payload (i.e. `extractions/{eo}_v{n}`.payload,
// already unwrapped by lib/db.ts — never pass the raw envelope here).

export interface LegacyDocLike {
  device_name?: string | null;
  manufacturer?: string | null;
  category?: string | null;
  part_numbers?: string[];
  fitment_count?: number;
}

export interface AgentExtractionLike {
  device_name?: string | null;
  manufacturer?: string | null;
  category?: string | null;
  part_numbers?: string[];
  fitment?: unknown[];
}

export interface DiffField {
  name: string;
  legacy: unknown;
  agent: unknown;
  changed: boolean;
}

export interface DiffResult {
  fields: DiffField[];
  partNumbers: { added: string[]; removed: string[]; kept: string[] };
  fitmentCounts: { legacy: number; agent: number };
}

const COMPARE_FIELDS = ['device_name', 'manufacturer', 'category'] as const;

export function diffLegacyAgent(
  legacy: LegacyDocLike | null | undefined,
  agent: AgentExtractionLike | null | undefined
): DiffResult {
  const fields: DiffField[] = COMPARE_FIELDS.map((name) => {
    const legacyVal = legacy?.[name] ?? null;
    const agentVal = agent?.[name] ?? null;
    return { name, legacy: legacyVal, agent: agentVal, changed: legacyVal !== agentVal };
  });

  const legacyPns = new Set(legacy?.part_numbers ?? []);
  const agentPns = new Set(agent?.part_numbers ?? []);
  const added = [...agentPns].filter((p) => !legacyPns.has(p)).sort();
  const removed = [...legacyPns].filter((p) => !agentPns.has(p)).sort();
  const kept = [...agentPns].filter((p) => legacyPns.has(p)).sort();

  return {
    fields,
    partNumbers: { added, removed, kept },
    fitmentCounts: {
      legacy: legacy?.fitment_count ?? 0,
      agent: agent?.fitment?.length ?? 0,
    },
  };
}
