import { useEffect, useState } from 'react';
import { parseCorrections } from '../lib/corrections';

// Same admin-token pattern as RunNow.tsx (the canonical implementation): token
// lives only in the browser's localStorage under this key, sent as the
// X-Admin-Token header on same-origin /api routes — never in a body or query.
const STORAGE_KEY = 'carblegal_admin';

type Status = 'idle' | 'loading' | 'error';
type ReviewStatus = 'open' | 'approved' | 'rejected';

interface Props {
  reviewId: string;
  initialStatus: ReviewStatus;
}

interface ResolveResponse {
  review_id?: string;
  action?: string;
  matches?: number;
}

export default function ReviewActions({ reviewId, initialStatus }: Props) {
  const [token, setToken] = useState('');
  const [correctionsText, setCorrectionsText] = useState('');
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

  async function submit(action: 'approve' | 'reject') {
    const { value: corrections, error } = parseCorrections(correctionsText);
    if (error) {
      setCorrectionsError(error);
      return;
    }
    setCorrectionsError(null);
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
          {matches !== null && <> · {matches} match{matches === 1 ? '' : 'es'}</>}
        </p>
      </div>
    );
  }

  return (
    <div className="review-actions">
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

      <label className="review-actions-label">
        Corrections (optional) — JSON object of fields to replace, e.g. {'{"confidence": 0.9}'} or{' '}
        {'{"fitment": [...]}'} — replaced wholesale, validated server-side.
        <textarea
          value={correctionsText}
          onChange={(e) => {
            setCorrectionsText(e.target.value);
            setCorrectionsError(null);
          }}
          placeholder='{"field": "value"}'
          rows={4}
          className="review-corrections-textarea"
        />
      </label>
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
