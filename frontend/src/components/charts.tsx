/**
 * Small, dependency-free charts for real API data. Every chart shows its
 * numbers as text as well - colour is never the only carrier of meaning.
 */
"use client";

import type { LucideIcon } from "lucide-react";
import * as React from "react";

import { cn, toneSoft, toneSolid, type Tone } from "./ui";

export interface Segment {
  label: string;
  value: number;
  tone: Tone;
}

/** One stacked bar + legend. For a distribution that adds up to a whole. */
export function SegmentedBar({ segments, label }: { segments: Segment[]; label: string }) {
  const total = segments.reduce((sum, s) => sum + s.value, 0);
  const visible = segments.filter((s) => s.value > 0);
  const summary = visible.map((s) => `${s.label} ${s.value}`).join(", ");

  return (
    <div>
      <div
        role="img"
        aria-label={`${label}: ${summary || "no data"}`}
        className="flex h-2.5 w-full gap-0.5 overflow-hidden rounded-full bg-border/60"
      >
        {total > 0
          ? visible.map((s) => (
              <div
                key={s.label}
                className={cn("h-full transition-all duration-500 first:rounded-l-full last:rounded-r-full", toneSolid[s.tone])}
                style={{ width: `${(s.value / total) * 100}%` }}
              />
            ))
          : null}
      </div>
      <ul className="mt-3 flex flex-wrap gap-x-4 gap-y-1.5 text-xs">
        {segments.map((s) => (
          <li key={s.label} className="flex items-center gap-1.5 text-muted">
            <span aria-hidden className={cn("h-2 w-2 rounded-full", toneSolid[s.tone])} />
            <span className="capitalize">{s.label}</span>
            <span className="font-medium tabular-nums text-fg">{s.value}</span>
          </li>
        ))}
      </ul>
    </div>
  );
}

export interface BarItem {
  label: string;
  value: number;
  tone?: Tone;
  icon?: LucideIcon;
  href?: string;
}

/** Horizontal bars scaled to the largest value, each with its count. */
export function BarList({
  items,
  renderLink,
}: {
  items: BarItem[];
  /** Wrap a row in a link; kept as a render prop so this file stays router-agnostic. */
  renderLink?: (item: BarItem, row: React.ReactNode) => React.ReactNode;
}) {
  const max = Math.max(1, ...items.map((i) => i.value));
  return (
    <ul className="space-y-1">
      {items.map((item) => {
        const Icon = item.icon;
        const tone = item.tone ?? "accent";
        const row = (
          <div className="group flex items-center gap-3 rounded-lg px-2 py-1.5 transition-colors hover:bg-surface-2">
            {Icon ? (
              <span className={cn("flex h-7 w-7 shrink-0 items-center justify-center rounded-md", toneSoft[tone])}>
                <Icon aria-hidden className="h-3.5 w-3.5" />
              </span>
            ) : null}
            <div className="min-w-0 flex-1">
              <div className="flex items-center justify-between gap-2 text-sm">
                <span className="truncate capitalize text-fg">{item.label}</span>
                <span className="tabular-nums text-muted">{item.value}</span>
              </div>
              <div className="mt-1 h-1.5 w-full overflow-hidden rounded-full bg-border/60">
                <div
                  className={cn("h-full rounded-full transition-all duration-500", toneSolid[tone])}
                  style={{ width: `${(item.value / max) * 100}%` }}
                />
              </div>
            </div>
          </div>
        );
        return <li key={item.label}>{renderLink ? renderLink(item, row) : row}</li>;
      })}
    </ul>
  );
}

/** A single percentage as a ring, with the number in the middle. */
export function RingMeter({
  value,
  label,
  tone = "accent",
  size = 88,
}: {
  /** 0..1 */
  value: number;
  label: string;
  tone?: Tone;
  size?: number;
}) {
  const clamped = Math.max(0, Math.min(1, value));
  const stroke = 8;
  const r = (size - stroke) / 2;
  const circumference = 2 * Math.PI * r;
  const pct = Math.round(clamped * 100);
  const colour: Record<Tone, string> = {
    neutral: "text-muted",
    ok: "text-ok",
    warn: "text-warn",
    danger: "text-danger",
    accent: "text-accent",
    info: "text-info",
    violet: "text-accent2",
  };

  return (
    <div className="relative inline-flex items-center justify-center" role="img" aria-label={`${label}: ${pct}%`}>
      <svg width={size} height={size} className="-rotate-90">
        <circle cx={size / 2} cy={size / 2} r={r} fill="none" strokeWidth={stroke} className="stroke-border/70" />
        <circle
          cx={size / 2}
          cy={size / 2}
          r={r}
          fill="none"
          strokeWidth={stroke}
          strokeLinecap="round"
          stroke="currentColor"
          strokeDasharray={circumference}
          strokeDashoffset={circumference * (1 - clamped)}
          className={cn("transition-[stroke-dashoffset] duration-700", colour[tone])}
        />
      </svg>
      <span className="absolute text-lg font-semibold tabular-nums text-fg">{pct}%</span>
    </div>
  );
}
