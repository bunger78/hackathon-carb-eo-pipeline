import { useEffect, useState } from 'react';

// Same admin-token pattern as RunNow.tsx/ReviewActions.tsx (the canonical
// implementation): token lives only in the browser's localStorage under this
// key, sent as the X-Admin-Token header on same-origin /api routes — never in
// a body or query.
const STORAGE_KEY = 'carblegal_admin';

type Status = 'idle' | 'loading' | 'done' | 'error';

interface Props {
  eoNumber: string;
}

export default function RetryEo({ eoNumber }: Props) {
  const [token, setToken] = useState('');
  const [status, setStatus] = useState<Status>('idle');
  const [message, setMessage] = useState<string | null>(null);

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

  async function handleRetry() {
    setStatus('loading');
    setMessage(null);
    try {
      const res = await fetch('/api/retry-eo', {
        method: 'POST',
        headers: { 'X-Admin-Token': token, 'Content-Type': 'application/json' },
        body: JSON.stringify({ eo_number: eoNumber }),
      });
      const data = await res.json().catch(() => null);

      if (res.status === 401) {
        setStatus('error');
        setMessage('Bad admin token.');
        return;
      }
      if (res.status === 404) {
        setStatus('error');
        setMessage('No work item found for this EO.');
        return;
      }
      if (res.status === 409) {
        setStatus('error');
        const detail = data && typeof data === 'object' ? (data as { detail?: { status?: string } }).detail : undefined;
        setMessage(detail?.status ? `Not failed anymore (status: ${detail.status}).` : 'This EO is not currently failed.');
        return;
      }
      if (!res.ok) {
        setStatus('error');
        const detail = data && typeof (data as { detail?: unknown }).detail === 'string' ? (data as { detail: string }).detail : null;
        setMessage(detail ?? `Request failed (${res.status}).`);
        return;
      }

      setStatus('done');
      setMessage('requeued — will process on the next run.');
    } catch {
      setStatus('error');
      setMessage('Network error — check connection.');
    }
  }

  if (status === 'done') {
    return (
      <div className="run-now">
        <p className="review-resolved-banner">{message}</p>
      </div>
    );
  }

  return (
    <div className="run-now">
      <div className="run-now-row">
        <label className="run-now-label">
          Admin token
          <input
            type="password"
            value={token}
            onChange={(e) => handleTokenChange(e.target.value)}
            placeholder="admin token"
            className="run-now-input"
          />
        </label>
        <button type="button" onClick={handleRetry} disabled={status === 'loading' || !token} className="run-now-button">
          {status === 'loading' ? 'Retrying…' : 'Retry'}
        </button>
      </div>
      {message && <p className="run-now-message">{message}</p>}
    </div>
  );
}
