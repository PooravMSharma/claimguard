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