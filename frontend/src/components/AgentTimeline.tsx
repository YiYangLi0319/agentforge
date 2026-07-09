import {
  Bot,
  CheckCircle2,
  ChevronDown,
  ChevronRight,
  CircleDashed,
  Database,
  Loader2,
  ShieldAlert,
  ShieldCheck,
  Wrench,
  XCircle,
  Zap,
} from "lucide-react";
import { useState } from "react";

import type { AgentEvent } from "../lib/types";

/** Agent 执行过程时间线：从事件流增量构建（工具调用/审批/步骤/子 Agent/护栏/缓存） */

export interface TimelineItem {
  key: string;
  kind: "step" | "tool" | "approval" | "info" | "guardrail" | "cache";
  agent?: string | null;
  title: string;
  detail?: string;
  arguments?: Record<string, unknown>;
  status: "running" | "ok" | "error" | "pending";
  durationMs?: number;
}

export function reduceTimeline(items: TimelineItem[], ev: AgentEvent): TimelineItem[] {
  switch (ev.type) {
    case "step_started":
      return [
        ...items,
        {
          key: `step-${ev.agent ?? ""}-${ev.step}`,
          kind: "step",
          agent: ev.agent,
          title: `第 ${ev.step} 步 · 思考中`,
          status: "running",
        },
      ];
    case "step_finished":
      return items.map((it) =>
        it.key === `step-${ev.agent ?? ""}-${ev.step}` && it.status === "running"
          ? { ...it, title: `第 ${ev.step} 步`, status: "ok" }
          : it,
      );
    case "tool_started":
      return [
        ...items,
        {
          key: `tool-${ev.tool_call_id}`,
          kind: "tool",
          agent: ev.agent,
          title: ev.tool ?? "工具",
          arguments: ev.arguments,
          status: "running",
        },
      ];
    case "tool_finished":
      return items.map((it) =>
        it.key === `tool-${ev.tool_call_id}`
          ? {
              ...it,
              status: ev.ok ? "ok" : "error",
              detail: ev.result_preview,
              durationMs: ev.duration_ms,
            }
          : it,
      );
    case "approval_required":
      return [
        ...items,
        {
          key: `approval-${ev.tool_call_id}`,
          kind: "approval",
          agent: ev.agent,
          title: `等待审批 · ${ev.tool}`,
          arguments: ev.arguments,
          status: "pending",
        },
      ];
    case "approval_decided":
      return items.map((it) =>
        it.key === `approval-${ev.tool_call_id}`
          ? {
              ...it,
              title: ev.approved ? `已批准 · ${it.title.split("· ")[1] ?? ""}` : `已拒绝 · ${it.title.split("· ")[1] ?? ""}`,
              status: ev.approved ? "ok" : "error",
            }
          : it,
      );
    case "memory_updated":
      return [
        ...items,
        {
          key: `mem-${items.length}`,
          kind: "info",
          title: `写入 ${ev.added} 条长期记忆`,
          status: "ok",
        },
      ];
    case "guardrail_triggered":
      return [
        ...items,
        {
          key: `guard-${items.length}`,
          kind: "guardrail",
          title:
            ev.verdict === "block"
              ? `护栏拦截 · ${(ev.categories ?? []).join(",")}`
              : `护栏处理 · ${(ev.categories ?? []).join(",")}`,
          detail: ev.detail,
          status: ev.verdict === "block" ? "error" : "ok",
        },
      ];
    case "cache_hit":
      return [
        ...items,
        {
          key: `cache-${items.length}`,
          kind: "cache",
          title: `语义缓存命中（相似度 ${((ev.similarity ?? 0) * 100).toFixed(1)}%）`,
          status: "ok",
        },
      ];
    default:
      return items;
  }
}

function StatusIcon({ status, kind }: { status: TimelineItem["status"]; kind: TimelineItem["kind"] }) {
  if (status === "running") return <Loader2 size={14} className="animate-spin text-indigo-400" />;
  if (status === "pending") return <ShieldAlert size={14} className="text-amber-400" />;
  if (kind === "cache") return <Zap size={14} className="text-amber-400" />;
  if (kind === "guardrail")
    return status === "error" ? (
      <ShieldAlert size={14} className="text-rose-400" />
    ) : (
      <ShieldCheck size={14} className="text-emerald-400" />
    );
  if (status === "error") return <XCircle size={14} className="text-rose-400" />;
  if (kind === "tool") return <Wrench size={14} className="text-emerald-400" />;
  if (kind === "info") return <Database size={14} className="text-sky-400" />;
  return <CheckCircle2 size={14} className="text-emerald-400" />;
}

function TimelineRow({ item }: { item: TimelineItem }) {
  const [open, setOpen] = useState(false);
  const expandable = Boolean(item.detail || (item.arguments && Object.keys(item.arguments).length));
  return (
    <div className="rounded-lg border border-zinc-800/80 bg-zinc-900/50">
      <button
        className="flex w-full items-center gap-2 px-2.5 py-2 text-left"
        onClick={() => expandable && setOpen(!open)}
      >
        <StatusIcon status={item.status} kind={item.kind} />
        <span className="flex-1 truncate text-xs text-zinc-300">
          {item.kind === "tool" && <span className="mr-1 font-mono text-indigo-300">{item.title}</span>}
          {item.kind !== "tool" && item.title}
          {item.agent && (
            <span className="ml-1.5 rounded bg-zinc-800 px-1 py-0.5 text-[10px] text-zinc-400">
              <Bot size={9} className="mr-0.5 inline" />
              {item.agent}
            </span>
          )}
        </span>
        {item.durationMs !== undefined && (
          <span className="font-mono text-[10px] text-zinc-500">{item.durationMs}ms</span>
        )}
        {expandable &&
          (open ? (
            <ChevronDown size={13} className="text-zinc-500" />
          ) : (
            <ChevronRight size={13} className="text-zinc-500" />
          ))}
      </button>
      {open && (
        <div className="border-t border-zinc-800/80 px-3 py-2 text-[11px]">
          {item.arguments && Object.keys(item.arguments).length > 0 && (
            <div className="mb-1.5">
              <div className="mb-0.5 text-zinc-500">参数</div>
              <pre className="overflow-x-auto rounded bg-zinc-950 p-2 font-mono text-[11px] text-zinc-300">
                {JSON.stringify(item.arguments, null, 2)}
              </pre>
            </div>
          )}
          {item.detail && (
            <div>
              <div className="mb-0.5 text-zinc-500">结果</div>
              <pre className="max-h-40 overflow-auto rounded bg-zinc-950 p-2 font-mono text-[11px] whitespace-pre-wrap text-zinc-300">
                {item.detail}
              </pre>
            </div>
          )}
        </div>
      )}
    </div>
  );
}

export default function AgentTimeline({ items, running }: { items: TimelineItem[]; running: boolean }) {
  if (items.length === 0 && !running) {
    return (
      <div className="flex h-full flex-col items-center justify-center gap-2 text-zinc-600">
        <CircleDashed size={22} />
        <div className="text-xs">发送消息后，这里会实时展示 Agent 的执行过程</div>
      </div>
    );
  }
  return (
    <div className="space-y-1.5">
      {items.map((it) => (
        <TimelineRow key={it.key} item={it} />
      ))}
    </div>
  );
}
