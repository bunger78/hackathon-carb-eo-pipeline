import { useEffect, useMemo, useState } from 'react';
import { buildCorrections, type EditablePayload, type FitmentRowValue } from '../lib/buildCorrections';
import { parseCorrections } from '../lib/corrections';

// Same admin-token pattern as RunNow.tsx/ReviewActions.tsx (the canonical
// implementation): token lives only in the browser's localStorage under this
// key, sent as the X-Admin-Token header on same-origin /api routes — never in
// a body or query.
const STORAGE_KEY = 'carblegal_admin';

type Status = 'idle' | 'loading' | 'error';
type ReviewStatus = 'open' | 'approved' | 'rejected';

const CATEGORIES = ['intake', 'boost', 'cat', 'engine', 'exhaust', 'ignition', 'tune', 'other'] as const;
const INDUCTIONS = ['NA', 'TURBO', 'SC'] as const;

interface Props {
  reviewId: string;
  initialStatus: ReviewStatus;
  /** review.payload (see lib/db.ts's ReviewDoc) — shape varies by review `reason`, so every read is defensive. */
  payload: unknown;
}

interface ResolveResponse {
  review_id?: string;
  action?: string;
  matches?: number;
}

// --- form state shapes: every field is a raw text string (or '' for "blank")
// so partial/invalid typing is representable; parsed into typed values only
// at submit time (parseForm below). ---

interface ScalarFormState {
  manufacturer: string;
  device_name: string;
  description: string;
  category: string;
  issue_date: string;
  confidence: string;
}

interface FitmentFormRow {
  year_start: string;
  year_end: string;
  make: string;
  model: string;
  displacement_l: string;
  cylinders: string;
  induction: 'NA' | 'TURBO' | 'SC' | '';
  part_numbers: string;
  trim_note: string;
}

function emptyFitmentRow(): FitmentFormRow {
  return { year_start: '', year_end: '', make: '', model: '', displacement_l: '', cylinders: '', induction: '', part_numbers: '', trim_note: '' };
}

// --- payload -> form state normalization (mount-time only) ---

function asString(v: unknown): string {
  return typeof v === 'string' ? v : '';
}

function asInduction(v: unknown): 'NA' | 'TURBO' | 'SC' | '' {
  return v === 'NA' || v === 'TURBO' || v === 'SC' ? v : '';
}

function normalizeFitmentRow(v: unknown): FitmentFormRow {
  const row = (v && typeof v === 'object' ? v : {}) as Record<string, unknown>;
  const partNumbers = Array.isArray(row.part_numbers) ? row.part_numbers.filter((p): p is string => typeof p === 'string') : [];
  return {
    year_start: typeof row.year_start === 'number' ? String(row.year_start) : '',
    year_end: typeof row.year_end === 'number' ? String(row.year_end) : '',
    make: asString(row.make),
    model: asString(row.model),
    displacement_l: typeof row.displacement_l === 'number' ? String(row.displacement_l) : '',
    cylinders: typeof row.cylinders === 'number' ? String(row.cylinders) : '',
    induction: asInduction(row.induction),
    part_numbers: partNumbers.join(', '),
    trim_note: asString(row.trim_note),
  };
}

function normalizeScalars(payload: unknown): ScalarFormState {
  const p = (payload && typeof payload === 'object' ? payload : {}) as Record<string, unknown>;
  const category = typeof p.category === 'string' && (CATEGORIES as readonly string[]).includes(p.category) ? p.category : '';
  return {
    manufacturer: asString(p.manufacturer),
    device_name: asString(p.device_name),
    description: asString(p.description),
    category,
    issue_date: asString(p.issue_date),
    // confidence is required (non-nullable) server-side; a missing/malformed
    // value in the snapshot defaults to '0' rather than '' so an untouched
    // field never spuriously blocks submit as "required".
    confidence: typeof p.confidence === 'number' ? String(p.confidence) : '0',
  };
}

function normalizeFitmentRows(payload: unknown): FitmentFormRow[] {
  const p = (payload && typeof payload === 'object' ? payload : {}) as Record<string, unknown>;
  return Array.isArray(p.fitment) ? p.fitment.map(normalizeFitmentRow) : [];
}

// --- text -> typed value parsing (submit-time) ---

function parseNullableString(text: string): string | null {
  const t = text.trim();
  return t === '' ? null : t;
}

interface NumParse {
  value: number | null;
  error?: string;
}

function parseNullableInt(text: string): NumParse {
  const t = text.trim();
  if (t === '') return { value: null };
  const n = Number(t);
  if (!Number.isFinite(n)) return { value: null, error: 'must be a number' };
  if (!Number.isInteger(n)) return { value: null, error: 'must be a whole number' };
  return { value: n };
}

function parseNullableFloat(text: string): NumParse {
  const t = text.trim();
  if (t === '') return { value: null };
  const n = Number(t);
  if (!Number.isFinite(n)) return { value: null, error: 'must be a number' };
  return { value: n };
}

function parseConfidence(text: string): { value: number; error?: string } {
  const t = text.trim();
  if (t === '') return { value: 0, error: 'confidence is required' };
  const n = Number(t);
  if (!Number.isFinite(n)) return { value: 0, error: 'confidence must be a number' };
  if (n < 0 || n > 1) return { value: 0, error: 'confidence must be between 0 and 1' };
  return { value: n };
}

function parsePartNumbers(text: string): string[] {
  return text
    .split(',')
    .map((s) => s.trim())
    .filter(Boolean);
}

interface FormParseResult {
  value: EditablePayload | null;
  errors: string[];
}

function parseForm(scalars: ScalarFormState, fitmentRows: FitmentFormRow[]): FormParseResult {
  const errors: string[] = [];

  const confidence = parseConfidence(scalars.confidence);
  if (confidence.error) errors.push(confidence.error);

  const fitment: FitmentRowValue[] = fitmentRows.map((row, i) => {
    const yearStart = parseNullableInt(row.year_start);
    const yearEnd = parseNullableInt(row.year_end);
    const displacement = parseNullableFloat(row.displacement_l);
    const cylinders = parseNullableInt(row.cylinders);
    if (yearStart.error) errors.push(`Row ${i + 1} year_start: ${yearStart.error}`);
    if (yearEnd.error) errors.push(`Row ${i + 1} year_end: ${yearEnd.error}`);
    if (displacement.error) errors.push(`Row ${i + 1} displacement_l: ${displacement.error}`);
    if (cylinders.error) errors.push(`Row ${i + 1} cylinders: ${cylinders.error}`);
    return {
      year_start: yearStart.value,
      year_end: yearEnd.value,
      make: parseNullableString(row.make),
      model: parseNullableString(row.model),
      displacement_l: displacement.value,
      cylinders: cylinders.value,
      induction: row.induction === '' ? null : row.induction,
      part_numbers: parsePartNumbers(row.part_numbers),
      trim_note: parseNullableString(row.trim_note),
    };
  });

  if (errors.length > 0) return { value: null, errors };

  return {
    value: {
      manufacturer: parseNullableString(scalars.manufacturer),
      device_name: parseNullableString(scalars.device_name),
      description: parseNullableString(scalars.description),
      category: scalars.category === '' ? null : scalars.category,
      issue_date: parseNullableString(scalars.issue_date),
      confidence: confidence.value,
      fitment,
    },
    errors: [],
  };
}

export default function ReviewEditor({ reviewId, initialStatus, payload }: Props) {
  // Immutable snapshot of the review's original payload, parsed once — the
  // diff base for buildCorrections(). Falls back to an all-blank payload if
  // the snapshot itself fails to parse (defensive only; shouldn't happen for
  // already-validated data).
  const original = useMemo<EditablePayload>(() => {
    const parsed = parseForm(normalizeScalars(payload), normalizeFitmentRows(payload));
    return (
      parsed.value ?? {
        manufacturer: null,
        device_name: null,
        description: null,
        category: null,
        issue_date: null,
        confidence: 0,
        fitment: [],
      }
    );
    // Intentionally computed once from the initial `payload` prop only — this
    // is the fixed diff base for buildCorrections(), not something that should
    // re-derive as the user edits the form.
  }, []);

  const [token, setToken] = useState('');
  const [scalars, setScalars] = useState<ScalarFormState>(() => normalizeScalars(payload));
  const [fitmentRows, setFitmentRows] = useState<FitmentFormRow[]>(() => normalizeFitmentRows(payload));

  // Which correction source wins on submit: the structured form, or the raw
  // JSON fallback in the "Advanced" <details> — whichever was edited most
  // recently. Editing the form flips this back to false even after the raw
  // JSON box has been touched.
  const [useRawJson, setUseRawJson] = useState(false);
  const [rawJsonText, setRawJsonText] = useState('');

  const [formErrors, setFormErrors] = useState<string[]>([]);
  const [correctionsError, setCorrectionsError] = useState<string | null>(null);
  const [status, setStatus] = useState<Status>('idle');
  const [message, setMessage] = useState<string | null>(null);
  const [resolved, setResolved] = useState<ReviewStatus>(initialStatus);
  const [matches, setMatches] = useState<number | null>(null);

  useEffect(() => {
    try {
      const saved = localStorage.getItem(STORAGE_KEY);
      if (saved) setToken(saved);
    } catch {
      // localStorage unavailable (private mode, etc.) — token input still works this session
    }
  }, []);

  function handleTokenChange(value: string) {
    setToken(value);
    try {
      localStorage.setItem(STORAGE_KEY, value);
    } catch {
      // ignore storage failures
    }
  }

  function updateScalar(key: keyof ScalarFormState, value: string) {
    setUseRawJson(false);
    setScalars((s) => ({ ...s, [key]: value }));
  }

  function updateFitmentField(index: number, key: keyof FitmentFormRow, value: string) {
    setUseRawJson(false);
    setFitmentRows((rows) => rows.map((r, i) => (i === index ? { ...r, [key]: value } : r)));
  }

  function addFitmentRow() {
    setUseRawJson(false);
    setFitmentRows((rows) => [...rows, emptyFitmentRow()]);
  }

  function removeFitmentRow(index: number) {
    setUseRawJson(false);
    setFitmentRows((rows) => rows.filter((_, i) => i !== index));
  }

  function handleRawJsonChange(value: string) {
    setUseRawJson(true);
    setRawJsonText(value);
    setCorrectionsError(null);
  }

  async function submit(action: 'approve' | 'reject') {
    let corrections: Record<string, unknown> | null;

    if (useRawJson) {
      const { value, error } = parseCorrections(rawJsonText);
      if (error) {
        setCorrectionsError(error);
        return;
      }
      setCorrectionsError(null);
      setFormErrors([]);
      corrections = value;
    } else {
      const { value, errors } = parseForm(scalars, fitmentRows);
      if (errors.length > 0) {
        setFormErrors(errors);
        return;
      }
      setFormErrors([]);
      setCorrectionsError(null);
      corrections = value ? buildCorrections(original, value) : null;
    }

    setStatus('loading');
    setMessage(null);

    try {
      const res = await fetch('/api/review-action', {
        method: 'POST',
        headers: { 'X-Admin-Token': token, 'Content-Type': 'application/json' },
        body: JSON.stringify({ review_id: reviewId, action, corrections }),
      });
      const data = await res.json().catch(() => null);

      if (res.status === 401) {
        setStatus('error');
        setMessage('Bad admin token.');
        return;
      }
      if (res.status === 404) {
        setStatus('error');
        setMessage('Review not found — it may have been deleted.');
        return;
      }
      if (res.status === 409) {
        setStatus('error');
        const detail = data && typeof data === 'object' ? (data as { detail?: { status?: string } }).detail : undefined;
        setMessage(detail?.status ? `Already resolved (status: ${detail.status}).` : 'This review was already resolved.');
        return;
      }
      if (res.status === 422) {
        setStatus('error');
        const detail = data && typeof (data as { detail?: unknown }).detail === 'string' ? (data as { detail: string }).detail : null;
        setMessage(detail ?? 'The pipeline rejected this action or its corrections.');
        return;
      }
      if (res.status === 503) {
        setStatus('error');
        setMessage('Budget exceeded — try again later.');
        return;
      }
      if (!res.ok) {
        setStatus('error');
        const detail = data && typeof (data as { detail?: unknown }).detail === 'string' ? (data as { detail: string }).detail : null;
        setMessage(detail ?? `Request failed (${res.status}).`);
        return;
      }

      const parsed = (data ?? {}) as ResolveResponse;
      setStatus('idle');
      setMessage(null);
      setMatches(typeof parsed.matches === 'number' ? parsed.matches : null);
      setResolved(action === 'approve' ? 'approved' : 'rejected');
    } catch {
      setStatus('error');
      setMessage('Network error — check connection.');
    }
  }

  if (resolved !== 'open') {
    return (
      <div className="review-actions">
        <p className="review-resolved-banner">
          Pipeline resumed — status: {resolved}
          {matches !== null && (
            <>
              {' '}
              · {matches} match{matches === 1 ? '' : 'es'}
            </>
          )}
        </p>
      </div>
    );
  }

  const confidenceInvalid = formErrors.some((e) => e.startsWith('confidence'));

  return (
    <div className="review-editor">
      <label className="review-actions-label">
        Admin token
        <input
          type="password"
          value={token}
          onChange={(e) => handleTokenChange(e.target.value)}
          placeholder="admin token"
          className="run-now-input"
        />
      </label>

      <div className="review-editor-scalars">
        <label className="review-editor-field">
          Manufacturer
          <input type="text" value={scalars.manufacturer} onChange={(e) => updateScalar('manufacturer', e.target.value)} />
        </label>
        <label className="review-editor-field">
          Device name
          <input type="text" value={scalars.device_name} onChange={(e) => updateScalar('device_name', e.target.value)} />
        </label>
        <label className="review-editor-field">
          Category
          <select value={scalars.category} onChange={(e) => updateScalar('category', e.target.value)}>
            <option value="">—</option>
            {CATEGORIES.map((c) => (
              <option key={c} value={c}>
                {c}
              </option>
            ))}
          </select>
        </label>
        <label className="review-editor-field">
          Issue date
          <input type="text" value={scalars.issue_date} onChange={(e) => updateScalar('issue_date', e.target.value)} placeholder="YYYY-MM-DD" />
        </label>
        <label className="review-editor-field">
          Confidence
          <input
            type="number"
            min={0}
            max={1}
            step={0.05}
            value={scalars.confidence}
            onChange={(e) => updateScalar('confidence', e.target.value)}
            className={confidenceInvalid ? 'input-invalid' : ''}
          />
        </label>
        <label className="review-editor-field review-editor-field-wide">
          Description
          <textarea
            value={scalars.description}
            onChange={(e) => updateScalar('description', e.target.value)}
            rows={3}
            className="review-corrections-textarea"
          />
        </label>
      </div>

      <div>
        <h3>Fitment</h3>
        <div className="data-table-wrap">
          <table className="data-table fitment-table">
            <thead>
              <tr>
                <th>Year start</th>
                <th>Year end</th>
                <th>Make</th>
                <th>Model</th>
                <th>Displacement (L)</th>
                <th>Cylinders</th>
                <th>Induction</th>
                <th>Part numbers</th>
                <th>Trim note</th>
                <th></th>
              </tr>
            </thead>
            <tbody>
              {fitmentRows.length === 0 ? (
                <tr>
                  <td colSpan={10} className="section-note">
                    No fitment rows.
                  </td>
                </tr>
              ) : (
                fitmentRows.map((row, i) => (
                  <tr key={i}>
                    <td>
                      <input type="text" value={row.year_start} onChange={(e) => updateFitmentField(i, 'year_start', e.target.value)} />
                    </td>
                    <td>
                      <input type="text" value={row.year_end} onChange={(e) => updateFitmentField(i, 'year_end', e.target.value)} />
                    </td>
                    <td>
                      <input type="text" value={row.make} onChange={(e) => updateFitmentField(i, 'make', e.target.value)} />
                    </td>
                    <td>
                      <input type="text" value={row.model} onChange={(e) => updateFitmentField(i, 'model', e.target.value)} />
                    </td>
                    <td>
                      <input type="text" value={row.displacement_l} onChange={(e) => updateFitmentField(i, 'displacement_l', e.target.value)} />
                    </td>
                    <td>
                      <input type="text" value={row.cylinders} onChange={(e) => updateFitmentField(i, 'cylinders', e.target.value)} />
                    </td>
                    <td>
                      <select value={row.induction} onChange={(e) => updateFitmentField(i, 'induction', e.target.value)}>
                        <option value="">—</option>
                        {INDUCTIONS.map((opt) => (
                          <option key={opt} value={opt}>
                            {opt}
                          </option>
                        ))}
                      </select>
                    </td>
                    <td>
                      <input
                        type="text"
                        value={row.part_numbers}
                        onChange={(e) => updateFitmentField(i, 'part_numbers', e.target.value)}
                        placeholder="A1, A2"
                      />
                    </td>
                    <td>
                      <textarea value={row.trim_note} onChange={(e) => updateFitmentField(i, 'trim_note', e.target.value)} rows={1} />
                    </td>
                    <td>
                      <button type="button" className="fitment-row-delete" onClick={() => removeFitmentRow(i)}>
                        Delete
                      </button>
                    </td>
                  </tr>
                ))
              )}
            </tbody>
          </table>
        </div>
        <button type="button" className="fitment-add-row" onClick={addFitmentRow}>
          + Add row
        </button>
      </div>

      {formErrors.length > 0 && (
        <ul className="review-editor-error-list">
          {formErrors.map((e, i) => (
            <li key={i} className="review-editor-error">
              {e}
            </li>
          ))}
        </ul>
      )}

      <details className="resolved-section">
        <summary>Advanced: raw JSON</summary>
        <label className="review-actions-label">
          Corrections (optional) — JSON object of fields to replace, e.g. {'{"confidence": 0.9}'} or{' '}
          {'{"fitment": [...]}'} — replaced wholesale, validated server-side. Editing this overrides the form above.
          <textarea
            value={rawJsonText}
            onChange={(e) => handleRawJsonChange(e.target.value)}
            placeholder='{"field": "value"}'
            rows={4}
            className="review-corrections-textarea"
          />
        </label>
      </details>
      {correctionsError && <p className="run-now-message">{correctionsError}</p>}

      <div className="review-actions-row">
        <button
          type="button"
          onClick={() => submit('approve')}
          disabled={status === 'loading' || !token}
          className="run-now-button review-approve-button"
        >
          {status === 'loading' ? 'Working…' : 'Approve'}
        </button>
        <button
          type="button"
          onClick={() => submit('reject')}
          disabled={status === 'loading' || !token}
          className="run-now-button review-reject-button"
        >
          {status === 'loading' ? 'Working…' : 'Reject'}
        </button>
      </div>
      {message && <p className="run-now-message">{message}</p>}
    </div>
  );
}
