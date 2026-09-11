/**
 * shadcn/ui-style primitives, colocated because the set is small and the app
 * uses every one of them. Adding a second component library would violate the
 * "no unnecessary technologies" rule.
 */
"use client";

import { clsx, type ClassValue } from "clsx";
import { AlertTriangle, Inbox, type LucideIcon } from "lucide-react";
import * as React from "react";
import { twMerge } from "tailwind-merge";

export function cn(...inputs: ClassValue[]) {
  return twMerge(clsx(inputs));
}

// ---------------------------------------------------------------------------
// Tones - one vocabulary for badges, stat icons, charts and status dots
// ---------------------------------------------------------------------------
export type Tone = "neutral" | "ok" | "warn" | "danger" | "accent" | "info" | "violet";

/** Soft tinted background + readable foreground, for chips and icon tiles. */
export const toneSoft: Record<Tone, string> = {
  neutral: "bg-border/60 text-muted",
  ok: "bg-ok/10 text-ok",
  warn: "bg-warn/10 text-warn",
  danger: "bg-danger/10 text-danger",
  accent: "bg-accent/10 text-accent",
  info: "bg-info/10 text-info",
  violet: "bg-accent2/10 text-accent2",
};

/** Solid fill, for bars, segments and dots. */
export const toneSolid: Record<Tone, string> = {
  neutral: "bg-muted/50",
  ok: "bg-ok",
  warn: "bg-warn",
  danger: "bg-danger",
  accent: "bg-accent",
  info: "bg-info",
  violet: "bg-accent2",
};

// ---------------------------------------------------------------------------
// Button
// ---------------------------------------------------------------------------
type ButtonVariant = "primary" | "secondary" | "ghost" | "danger";
type ButtonSize = "sm" | "md" | "lg";

const buttonVariants: Record<ButtonVariant, string> = {
  primary: "bg-brand text-white shadow-sm hover:shadow-glow hover:brightness-110",
  secondary:
    "border border-border bg-surface text-fg shadow-sm hover:border-accent/40 hover:bg-accent/5 hover:text-accent",
  ghost: "text-muted hover:bg-border/50 hover:text-fg",
  danger: "bg-danger text-white shadow-sm hover:brightness-110",
};

const buttonSizes: Record<ButtonSize, string> = {
  sm: "h-8 px-3 text-xs",
  md: "h-9 px-4 text-sm",
  lg: "h-11 px-5 text-sm",
};

export function Button({
  variant = "primary",
  size = "md",
  className,
  ...props
}: React.ButtonHTMLAttributes<HTMLButtonElement> & {
  variant?: ButtonVariant;
  size?: ButtonSize;
}) {
  return (
    <button
      className={cn(
        "inline-flex items-center justify-center gap-2 rounded-lg font-medium",
        "transition-all duration-150 active:scale-[0.98]",
        "focus-visible:outline focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-accent",
        "disabled:pointer-events-none disabled:opacity-50",
        buttonSizes[size],
        buttonVariants[variant],
        className,
      )}
      {...props}
    />
  );
}

// ---------------------------------------------------------------------------
// Card
// ---------------------------------------------------------------------------
export function Card({ className, ...props }: React.HTMLAttributes<HTMLDivElement>) {
  return (
    <div
      className={cn(
        "rounded-xl border border-border bg-surface shadow-card transition-shadow duration-200",
        className,
      )}
      {...props}
    />
  );
}

export function CardHeader({ className, ...props }: React.HTMLAttributes<HTMLDivElement>) {
  return <div className={cn("border-b border-border px-5 py-3.5", className)} {...props} />;
}

export function CardTitle({ className, ...props }: React.HTMLAttributes<HTMLHeadingElement>) {
  return (
    <h2
      className={cn("flex items-center gap-2 text-sm font-semibold text-fg", className)}
      {...props}
    />
  );
}

export function CardBody({ className, ...props }: React.HTMLAttributes<HTMLDivElement>) {
  return <div className={cn("p-5", className)} {...props} />;
}

// ---------------------------------------------------------------------------
// Badge
// ---------------------------------------------------------------------------
type BadgeTone = "neutral" | "ok" | "warn" | "danger" | "accent";

export function Badge({
  tone = "neutral",
  dot = false,
  className,
  children,
  ...props
}: React.HTMLAttributes<HTMLSpanElement> & { tone?: BadgeTone; dot?: boolean }) {
  return (
    <span
      className={cn(
        "inline-flex items-center gap-1.5 rounded-full px-2 py-0.5 text-[11px] font-medium",
        toneSoft[tone],
        className,
      )}
      {...props}
    >
      {dot ? <span aria-hidden className={cn("h-1.5 w-1.5 rounded-full", toneSolid[tone])} /> : null}
      {children}
    </span>
  );
}

export function statusTone(status: string): BadgeTone {
  if (["ready", "completed", "info", "ok"].includes(status)) return "ok";
  if (["failed", "critical", "error"].includes(status)) return "danger";
  if (["warning", "pending", "queued"].includes(status)) return "warn";
  if (["processing", "embedding", "chunking", "extracting", "transcribing"].includes(status))
    return "accent";
  return "neutral";
}

// ---------------------------------------------------------------------------
// Input / Select / Textarea
// ---------------------------------------------------------------------------
const fieldStyles =
  "w-full rounded-lg border border-border bg-surface px-3 py-2 text-sm text-fg shadow-sm " +
  "placeholder:text-muted/70 transition-colors " +
  "focus:border-accent focus:outline-none focus:ring-2 focus:ring-accent/25 disabled:opacity-50";

export const Input = React.forwardRef<HTMLInputElement, React.InputHTMLAttributes<HTMLInputElement>>(
  function Input({ className, ...props }, ref) {
    return <input ref={ref} className={cn(fieldStyles, className)} {...props} />;
  },
);

export const Textarea = React.forwardRef<
  HTMLTextAreaElement,
  React.TextareaHTMLAttributes<HTMLTextAreaElement>
>(function Textarea({ className, ...props }, ref) {
  return <textarea ref={ref} className={cn(fieldStyles, "resize-none", className)} {...props} />;
});

export function Select({
  className,
  ...props
}: React.SelectHTMLAttributes<HTMLSelectElement>) {
  return <select className={cn(fieldStyles, className)} {...props} />;
}

export function Label({ className, ...props }: React.LabelHTMLAttributes<HTMLLabelElement>) {
  return (
    <label className={cn("mb-1.5 block text-xs font-medium text-muted", className)} {...props} />
  );
}

// ---------------------------------------------------------------------------
// Table - always inside its own horizontal scroll container
// ---------------------------------------------------------------------------
export function Table({ className, ...props }: React.TableHTMLAttributes<HTMLTableElement>) {
  return (
    <div className="w-full overflow-x-auto">
      <table
        className={cn("w-full min-w-[36rem] text-sm [&_tbody_tr]:transition-colors [&_tbody_tr:hover]:bg-surface-2", className)}
        {...props}
      />
    </div>
  );
}

export function Th({ className, ...props }: React.ThHTMLAttributes<HTMLTableCellElement>) {
  return (
    <th
      className={cn(
        "border-b border-border bg-surface-2 px-4 py-2.5 text-left text-[11px] font-semibold uppercase tracking-wide text-muted",
        className,
      )}
      {...props}
    />
  );
}

export function Td({ className, ...props }: React.TdHTMLAttributes<HTMLTableCellElement>) {
  return <td className={cn("border-b border-border/60 px-4 py-2.5 text-fg", className)} {...props} />;
}

// ---------------------------------------------------------------------------
// State views - loading / empty / error are part of "done" for every view
// ---------------------------------------------------------------------------
export function Skeleton({ className }: { className?: string }) {
  return <div className={cn("animate-pulse rounded-lg bg-border/60", className)} />;
}

export function EmptyState({
  title,
  hint,
  action,
  icon: Icon = Inbox,
}: {
  title: string;
  hint?: string;
  action?: React.ReactNode;
  icon?: LucideIcon;
}) {
  return (
    <div className="flex flex-col items-center gap-2 px-4 py-12 text-center">
      <span className="mb-1 flex h-11 w-11 items-center justify-center rounded-full bg-accent/10 text-accent">
        <Icon aria-hidden className="h-5 w-5" />
      </span>
      <p className="text-sm font-medium text-fg">{title}</p>
      {hint ? <p className="max-w-sm text-xs text-muted">{hint}</p> : null}
      {action}
    </div>
  );
}

export function ErrorState({ message, onRetry }: { message: string; onRetry?: () => void }) {
  return (
    <div
      role="alert"
      className="flex items-start gap-3 rounded-xl border border-danger/30 bg-danger/5 p-4"
    >
      <AlertTriangle aria-hidden className="mt-0.5 h-4 w-4 shrink-0 text-danger" />
      <div className="flex flex-col items-start gap-2">
        <p className="text-sm font-medium text-danger">Something went wrong</p>
        <p className="text-xs text-muted">{message}</p>
        {onRetry ? (
          <Button variant="secondary" size="sm" onClick={onRetry}>
            Try again
          </Button>
        ) : null}
      </div>
    </div>
  );
}

// ---------------------------------------------------------------------------
// Stat tile
// ---------------------------------------------------------------------------
export function Stat({
  label,
  value,
  hint,
  tone,
  icon: Icon,
  iconTone = "accent",
}: {
  label: string;
  value: React.ReactNode;
  hint?: string;
  tone?: BadgeTone;
  icon?: LucideIcon;
  iconTone?: Tone;
}) {
  return (
    <Card className="group relative overflow-hidden p-4 hover:-translate-y-0.5 hover:shadow-lift">
      <div className="flex items-start justify-between gap-3">
        <p className="text-xs font-medium text-muted">{label}</p>
        {Icon ? (
          <span
            className={cn(
              "flex h-8 w-8 shrink-0 items-center justify-center rounded-lg transition-transform duration-200 group-hover:scale-110",
              toneSoft[iconTone],
            )}
          >
            <Icon aria-hidden className="h-4 w-4" />
          </span>
        ) : null}
      </div>
      <p
        className={cn(
          "mt-1 text-2xl font-semibold tracking-tight tabular-nums",
          tone === "danger" ? "text-danger" : tone === "warn" ? "text-warn" : "text-fg",
        )}
      >
        {value}
      </p>
      {hint ? <p className="mt-1 text-xs text-muted">{hint}</p> : null}
    </Card>
  );
}

// ---------------------------------------------------------------------------
// Confidence meter - reads as a number AND a bar, never colour alone
// ---------------------------------------------------------------------------
export function Confidence({ value }: { value: number }) {
  const pct = Math.round(value * 100);
  const tone = value >= 0.7 ? "bg-ok" : value >= 0.4 ? "bg-warn" : "bg-danger";
  return (
    <div className="flex items-center gap-2" title={`Confidence ${pct}%`}>
      <div className="h-1.5 w-16 overflow-hidden rounded-full bg-border">
        <div className={cn("h-full rounded-full", tone)} style={{ width: `${pct}%` }} />
      </div>
      <span className="text-xs tabular-nums text-muted">{pct}%</span>
    </div>
  );
}
