import { clsx, type ClassValue } from "clsx";
import { twMerge } from "tailwind-merge";

export function cn(...inputs: ClassValue[]): string {
  return twMerge(clsx(inputs));
}

export function fmtMyr(n: number | undefined | null): string {
  if (n == null) return "—";
  return new Intl.NumberFormat("en-MY", {
    style: "currency",
    currency: "MYR",
    maximumFractionDigits: 2
  }).format(n);
}

/**
 * Backend datetimes are UTC. If a serializer stripped the trailing `Z`
 * (pymongo returning naive datetimes pre-`tz_aware=True`), `Date.parse`
 * would silently treat the string as local time and skew everything by
 * the browser's offset (8h in Malaysia). Append a `Z` defensively when
 * the ISO string carries no timezone marker.
 */
function normalizeIso(iso: string): string {
  // Already has offset (`Z`, `+08:00`, `-0500`) — leave it alone.
  if (/[zZ]|[+-]\d{2}:?\d{2}$/.test(iso)) return iso;
  // Date-only strings (e.g. "2026-05-11") shouldn't get a TZ tacked on.
  if (!/\d{2}:\d{2}/.test(iso)) return iso;
  return iso + "Z";
}

export function fmtRelative(iso: string | undefined | null): string {
  if (!iso) return "—";
  const ts = Date.parse(normalizeIso(iso));
  if (Number.isNaN(ts)) return iso;
  const sec = (Date.now() - ts) / 1000;
  const abs = Math.abs(sec);
  const suffix = sec >= 0 ? "ago" : "from now";
  if (abs < 60) return `${Math.round(abs)}s ${suffix}`;
  if (abs < 3600) return `${Math.round(abs / 60)}m ${suffix}`;
  if (abs < 86400) return `${Math.round(abs / 3600)}h ${suffix}`;
  return `${Math.round(abs / 86400)}d ${suffix}`;
}

export function fmtDate(iso: string | undefined | null): string {
  if (!iso) return "—";
  try {
    return new Date(normalizeIso(iso)).toLocaleString("en-MY", {
      timeZone: "Asia/Kuala_Lumpur",
      dateStyle: "medium",
      timeStyle: "short"
    });
  } catch {
    return iso;
  }
}

export function severityClass(s: string | undefined): string {
  switch (s) {
    case "high":
      return "pill pill-danger";
    case "medium":
      return "pill pill-warn";
    case "low":
      return "pill pill-ok";
    default:
      return "pill pill-muted";
  }
}
