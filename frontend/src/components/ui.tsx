import clsx from "clsx";
import { Loader2 } from "lucide-react";
import type { ReactNode } from "react";

export function Button({
  children,
  onClick,
  variant = "primary",
  size = "md",
  disabled,
  loading,
  className,
  type = "button",
}: {
  children: ReactNode;
  onClick?: () => void;
  variant?: "primary" | "ghost" | "danger" | "outline";
  size?: "sm" | "md";
  disabled?: boolean;
  loading?: boolean;
  className?: string;
  type?: "button" | "submit";
}) {
  return (
    <button
      type={type}
      onClick={onClick}
      disabled={disabled || loading}
      className={clsx(
        "inline-flex items-center justify-center gap-1.5 rounded-lg font-medium transition-colors disabled:cursor-not-allowed disabled:opacity-50",
        size === "sm" ? "px-2.5 py-1.5 text-xs" : "px-4 py-2 text-sm",
        variant === "primary" && "bg-indigo-600 text-white hover:bg-indigo-500",
        variant === "ghost" && "text-zinc-300 hover:bg-zinc-800",
        variant === "outline" && "border border-zinc-700 text-zinc-300 hover:border-zinc-500 hover:text-zinc-100",
        variant === "danger" && "bg-rose-600/90 text-white hover:bg-rose-500",
        className,
      )}
    >
      {loading && <Loader2 size={14} className="animate-spin" />}
      {children}
    </button>
  );
}

export function Input({
  value,
  onChange,
  placeholder,
  type = "text",
  className,
  onKeyDown,
}: {
  value: string;
  onChange: (v: string) => void;
  placeholder?: string;
  type?: string;
  className?: string;
  onKeyDown?: (e: React.KeyboardEvent) => void;
}) {
  return (
    <input
      type={type}
      value={value}
      placeholder={placeholder}
      onChange={(e) => onChange(e.target.value)}
      onKeyDown={onKeyDown}
      className={clsx(
        "w-full rounded-lg border border-zinc-700/80 bg-zinc-900 px-3 py-2 text-sm text-zinc-200",
        "placeholder:text-zinc-600 focus:border-indigo-500 focus:ring-1 focus:ring-indigo-500/50 focus:outline-none",
        className,
      )}
    />
  );
}

export function Badge({
  children,
  tone = "zinc",
}: {
  children: ReactNode;
  tone?: "zinc" | "green" | "red" | "amber" | "indigo" | "sky";
}) {
  const tones: Record<string, string> = {
    zinc: "bg-zinc-500/15 text-zinc-300",
    green: "bg-emerald-500/15 text-emerald-300",
    red: "bg-rose-500/15 text-rose-300",
    amber: "bg-amber-500/15 text-amber-300",
    indigo: "bg-indigo-500/15 text-indigo-300",
    sky: "bg-sky-500/15 text-sky-300",
  };
  return (
    <span className={clsx("inline-flex items-center gap-1 rounded px-1.5 py-0.5 text-[11px] font-medium", tones[tone])}>
      {children}
    </span>
  );
}

export function statusTone(status: string): "green" | "red" | "amber" | "indigo" | "zinc" {
  if (status === "succeeded" || status === "ready") return "green";
  if (status === "failed" || status === "cancelled") return "red";
  if (status === "awaiting_approval") return "amber";
  if (status === "running" || status === "processing" || status === "pending") return "indigo";
  return "zinc";
}

export const STATUS_LABEL: Record<string, string> = {
  succeeded: "成功",
  failed: "失败",
  cancelled: "已取消",
  running: "运行中",
  awaiting_approval: "待审批",
  pending: "排队中",
  processing: "处理中",
  ready: "已就绪",
};

export function EmptyState({ icon, title, desc }: { icon: ReactNode; title: string; desc?: string }) {
  return (
    <div className="flex h-full min-h-40 flex-col items-center justify-center gap-2 text-zinc-600">
      {icon}
      <div className="text-sm font-medium text-zinc-500">{title}</div>
      {desc && <div className="max-w-sm text-center text-xs leading-5">{desc}</div>}
    </div>
  );
}

export function formatCost(cost: number): string {
  if (!cost) return "¥0";
  return cost < 0.01 ? `¥${cost.toFixed(4)}` : `¥${cost.toFixed(2)}`;
}

export function formatTime(iso: string): string {
  const d = new Date(iso);
  return d.toLocaleString("zh-CN", { month: "2-digit", day: "2-digit", hour: "2-digit", minute: "2-digit" });
}
