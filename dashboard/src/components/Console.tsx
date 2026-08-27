import { useEffect, useRef, useState } from 'react';
import { formatEventTime } from '../lib/format';

interface FeedEvent {
  ts: number | null;
  agent: string;
  action: string;
  eo: string;
}

const AGENT_CLASS: Record<string, string> = {
  scout: 'agent-scout',
  extractor: 'agent-extractor',
  auditor: 'agent-auditor',
  matchmaker: 'agent-matchmaker',
};

const POLL_MS = 3000;
const AGENT_COL = 9;
const ACTION_COL = 13;
const SCROLL_BOTTOM_SLACK = 24; // px — still counts as "at bottom" within this slack

// Pads to a fixed column width like a terminal, but always keeps at least one
// separating space even when the value itself already reaches (or exceeds) the
// target width — e.g. "matchmaker" is longer than most agent names.
function padCol(s: string, width: number): string {
  return s.length >= width ? `${s} ` : s.padEnd(width);
}

// `index` (position within the current feed array) only breaks ties when `ts`
// is missing — normal (ts-present) events keep a stable content-only key so a
// window-slide (older events rolling off the 50-event cap) doesn't force
// unrelated surviving rows to remount. Without the index fallback, two events
// in the same poll both lacking `ts` would collide on this key (and on the
// pulse-detection check below, which reuses it).
function eventKey(e: FeedEvent, index: number): string {
  return e.ts !== null ? `${e.ts}|${e.agent}|${e.action}|${e.eo}` : `null-ts|${index}|${e.agent}|${e.action}|${e.eo}`;
}

export default function Console() {
  const [events, setEvents] = useState<FeedEvent[]>([]);
  const [connected, setConnected] = useState(true);
  const [pulseKey, setPulseKey] = useState(0);
  const scrollRef = useRef<HTMLDivElement>(null);
  const stickToBottomRef = useRef(true);
  const lastLatestKeyRef = useRef<string | null>(null);

  useEffect(() => {
    let cancelled = false;

    async function poll() {
      try {
        const res = await fetch('/api/feed');
        if (!res.ok) throw new Error(String(res.status));
        const data: FeedEvent[] = await res.json();
        if (cancelled) return;
        // server returns newest-first; the console reads oldest-to-newest, top-to-bottom
        const chronological = [...data].reverse();
        const latestIndex = chronological.length - 1;
        const latestKey = latestIndex >= 0 ? eventKey(chronological[latestIndex], latestIndex) : null;
        if (latestKey !== lastLatestKeyRef.current) {
          lastLatestKeyRef.current = latestKey;
          setPulseKey((k) => k + 1);
        }
        setConnected(true);
        setEvents(chronological);
      } catch {
        if (!cancelled) setConnected(false);
      }
    }

    poll();
    const id = setInterval(poll, POLL_MS);
    return () => {
      cancelled = true;
      clearInterval(id);
    };
  }, []);

  useEffect(() => {
    const el = scrollRef.current;
    if (el && stickToBottomRef.current) {
      el.scrollTop = el.scrollHeight;
    }
  }, [events]);

  function handleScroll() {
    const el = scrollRef.current;
    if (!el) return;
    const distanceFromBottom = el.scrollHeight - el.scrollTop - el.clientHeight;
    stickToBottomRef.current = distanceFromBottom < SCROLL_BOTTOM_SLACK;
  }

  return (
    <div className="console">
      <div className="console-titlebar">
        <span key={pulseKey} className="console-dot" aria-hidden="true" />
        <span>Live agent console</span>
        {!connected && <span className="console-status-text">reconnecting…</span>}
      </div>
      <div className="console-feed" ref={scrollRef} onScroll={handleScroll}>
        {events.length === 0 ? (
          <div className="console-empty">No events yet — waiting for the next run.</div>
        ) : (
          events.map((e, i) => (
            <div className="console-line" key={eventKey(e, i)}>
              <span className="console-ts">[{formatEventTime(e.ts)}]</span>{' '}
              <span className={`console-agent ${AGENT_CLASS[e.agent] ?? ''}`}>{padCol(e.agent, AGENT_COL)}</span>
              <span className="console-action">{padCol(e.action, ACTION_COL)}</span>
              <span className="console-eo">{e.eo}</span>
            </div>
          ))
        )}
      </div>
    </div>
  );
}
