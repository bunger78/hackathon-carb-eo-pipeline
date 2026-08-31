import { useEffect, useState } from 'react';

const STORAGE_KEY = 'carblegal_admin';

type Status = 'idle' | 'loading' | 'done' | 'error';

export default function RunNow() {
  const [token, setToken] = useState('');
  const [status, setStatus] = useState<Status>('idle');
  const [message, setMessage] = useState<string | null>(null);
  const [summary, setSummary] = useState<Record<string, unknown> | null>(null);

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

  async function handleRunNow() {
    setStatus('loading');
    setMessage(null);
    setSummary(null);
    try {
      // Content-Type is load-bearing: Astro's CSRF check 403s a POST that
      // carries neither a form content-type nor an explicit one (never reaches
      // the route). Every /api caller must send application/json.
      const res = await fetch('/api/run-now', {
        method: 'POST',
        headers: { 'X-Admin-Token': token, 'Content-Type': 'application/json' },
        body: '{}',
      });
      if (res.status === 401) {
        setStatus('error');
        setMessage('bad token');
        return;
      }
      const data = await res.json().catch(() => null);
      if (!res.ok) {
        setStatus('error');
        const detail = data && typeof data.detail === 'string' ? data.detail : `run-now failed (${res.status})`;
        setMessage(detail);
        return;
      }
      setStatus('done');
      setSummary((data as Record<string, unknown>) ?? {});
    } catch {
      setStatus('error');
      setMessage('network error — check connection');
    }
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
        <button
          type="button"
          onClick={handleRunNow}
          disabled={status === 'loading' || !token}
          className="run-now-button"
        >
          {status === 'loading' ? 'Running…' : 'Run now'}
        </button>
      </div>
      {message && <p className="run-now-message">{message}</p>}
      {summary && <pre className="run-now-summary">{JSON.stringify(summary, null, 2)}</pre>}
    </div>
  );
}
