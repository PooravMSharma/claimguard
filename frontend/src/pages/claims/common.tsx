import { useEffect, useState } from "react";

import type { ClaimStatus } from "../../api/types";
import { STATUS_LABELS } from "../../api/types";

export const ACTIVE: ClaimStatus[] = ["received", "processing"];

export function StatusTag({ status }: { status: ClaimStatus }) {
  return <span className={`status-tag status-${status}`}>{STATUS_LABELS[status]}</span>;
}

export function fileSize(bytes: number): string {
  if (bytes < 1024) return `${bytes} B`;
  if (bytes < 1024 * 1024) return `${(bytes / 1024).toFixed(0)} KB`;
  return `${(bytes / 1048576).toFixed(1)} MB`;
}

export function when(value: string | null): string {
  return value ? new Date(value).toLocaleString("en-IN", { dateStyle: "medium", timeStyle: "short" }) : "—";
}

export function seconds(from: string | null, to: string | null): string | null {
  if (!from || !to) return null;
  const s = (new Date(to).getTime() - new Date(from).getTime()) / 1000;
  return s < 60 ? `${s.toFixed(1)} s` : `${Math.round(s / 60)} min`;
}

export function money(value: string | number | null | undefined, zeroAsDash = true): string | null {
  if (value === null || value === undefined || value === "") return null;
  const n = Number(value);
  if (zeroAsDash && n === 0) return null;
  const whole = Number.isInteger(n);
  return `₹${n.toLocaleString("en-IN", { minimumFractionDigits: whole ? 0 : 2, maximumFractionDigits: 2 })}`;
}

const BAND_TEXT = { red: "Red", amber: "Amber", green: "Green" } as const;

/** Risk band with its score. The word is always shown: colour is never the only signal. */
export function BandTag({ band, score }: { band: "red" | "amber" | "green" | null; score?: number | null }) {
  if (!band) return <span className="muted">—</span>;
  return (
    <span className={`band band-${band}`} title={score !== null && score !== undefined ? `Risk score ${score} of 100` : undefined}>
      {BAND_TEXT[band]}
      {score !== null && score !== undefined && <span className="band-score">{score}</span>}
    </span>
  );
}

/** Time left before the review deadline: amber in the last quarter of the window, red when overdue. */
export function TimeLeft({ due, done, now }: { due: string | null; done?: boolean; now: number }) {
  if (!due) return <span className="muted">—</span>;
  if (done) return <span className="muted">—</span>;
  const ms = new Date(due).getTime() - now;
  const abs = Math.abs(ms);
  const h = Math.floor(abs / 3_600_000);
  const m = Math.floor((abs % 3_600_000) / 60_000);
  const text = h >= 48 ? `${Math.round(h / 24)} days` : h > 0 ? `${h} h ${m} min` : `${m} min`;
  if (ms < 0) return <span className="sla sla-over" title={`Due ${when(due)}`}>Overdue {text}</span>;
  const tight = ms < 45 * 60_000 || (h >= 48 && ms < 3 * 86_400_000);
  return (
    <span className={`sla ${tight ? "sla-soon" : ""}`} title={`Due ${when(due)}`}>
      {text} left
    </span>
  );
}

/** Re-render every 30 seconds so deadlines count down. */
export function useNow(intervalMs = 30_000): number {
  const [now, setNow] = useState(Date.now());
  useEffect(() => {
    const t = setInterval(() => setNow(Date.now()), intervalMs);
    return () => clearInterval(t);
  }, [intervalMs]);
  return now;
}
