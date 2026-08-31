import { readFileSync } from 'node:fs';
import { fileURLToPath } from 'node:url';
import { describe, expect, it } from 'vitest';

// Drift tripwire: the architecture diagram is hand-authored SVG, not generated
// from pipeline/workflow_graph.py, so nothing forces it to stay in sync. This
// reads the raw page source and asserts every workflow node name and service
// name it depicts is still present verbatim.
const source = readFileSync(fileURLToPath(new URL('./architecture.astro', import.meta.url)), 'utf-8');

describe('architecture page', () => {
  it('names every ADK 2 workflow node exactly as in pipeline/workflow_graph.py', () => {
    for (const nodeName of ['scout', 'heal', 'claim', 'process', 'summarize']) {
      expect(source).toContain(nodeName);
    }
  });

  it('names every service in the topology', () => {
    for (const serviceName of ['Cloud Scheduler', 'carb-api', 'Firestore', 'Gemini 3.7 Flash', 'carb-dash']) {
      expect(source).toContain(serviceName);
    }
  });
});
