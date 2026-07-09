import {
  CheckCircle2,
  ClipboardList,
  History,
  Loader2,
  RefreshCw,
  Search,
  SendHorizonal,
  Telescope,
  XCircle,
} from "lucide-react";
import { useCallback, useEffect, useRef, useState } from "react";

import Markdown from "../components/Markdown";
import { Badge, Button, EmptyState, formatTime, statusTone, STATUS_LABEL } from "../components/ui";
import { api } from "../lib/api";
import { streamRunEvents } from "../lib/sse";
import type { AgentEvent, ResearchPlan, ResearchReportInfo, Source } from "../lib/types";

interface WorkerState {
  task_id: string;
  title: string;
  agent?: string | null;
  status: "running" | "ok" | "failed";
  summary?: string;
  evidence_count?: number;
}

export default function ResearchPage() {
  const [query, setQuery] = useState("");
  const [running, setRunning] = useState(false);
  const [plan, setPlan] = useState<ResearchPlan | null>(null);
  const [workers, setWorkers] = useState<WorkerState[]>([]);
  const [reportText, setReportText] = useState("");
  const [review, setReview] = useState<{ passed: boolean; scores: Record<string, number>; feedback: string } | null>(null);
  const [sources, setSources] = useState<Source[]>([]);
  const [error, setError] = useState("");
  const [history, setHistory] = useState<ResearchReportInfo[]>([]);
  const [viewing, setViewing] = useState<string | null>(null);
  const abortRef = useRef<(() => void) | null>(null);

  const loadHistory = useCallback(() => {
    api.get<ResearchReportInfo[]>("/api/research").then(setHistory).catch(() => undefined);
  }, []);

  useEffect(() => {
    loadHistory();
    return () => abortRef.current?.();
  }, [loadHistory]);

  const reset = () => {
    setPlan(null);
    setWorkers([]);
    setReportText("");
    setReview(null);
    setSources([]);
    setError("");
    setViewing(null);
  };

  const handleEvent = useCallback((ev: AgentEvent) => {
    switch (ev.type) {
      case "plan_created":
        if (ev.plan) setPlan(ev.plan);
        break;
      case "research_task_started":
        setWorkers((w) => [
          ...w,
          { task_id: ev.task_id!, title: ev.title ?? "", agent: ev.agent, status: "running" },
        ]);
        break;
      case "research_task_finished":
        setWorkers((w) =>
          w.map((x) =>
            x.task_id === ev.task_id
              ? { ...x, status: ev.ok ? "ok" : "failed", summary: ev.summary, evidence_count: ev.evidence_count }
              : x,
          ),
        );
        break;
      case "llm_delta":
        if (ev.channel === "report" && ev.text) setReportText((t) => t + ev.text);
        break;
      case "report_draft":
        if (ev.markdown) setReportText(ev.markdown);
        break;
      case "report_review":
        setReview({ passed: ev.passed ?? true, scores: ev.scores ?? {}, feedback: ev.feedback ?? "" });
        if (ev.passed === false) setReportText("");
        break;
      case "sources_updated":
        if (ev.sources) setSources(ev.sources);
        break;
      case "run_finished":
        if (ev.output?.report) setReportText(String(ev.output.report));
        if (ev.output?.sources) setSources(ev.output.sources);
        setRunning(false);
        break;
      case "run_failed":
        setError(ev.error ?? "研究任务失败");
        setRunning(false);
        break;
    }
  }, []);

  const start = async () => {
    const q = query.trim();
    if (q.length < 4 || running) return;
    reset();
    setRunning(true);
    try {
      const resp = await api.post<{ run_id: string; report_id: string }>("/api/research", { query: q });
      abortRef.current = streamRunEvents(resp.run_id, {
        onEvent: handleEvent,
        onDone: loadHistory,
        onError: () => {
          setRunning(false);
          setError("事件流中断，可稍后在历史记录中查看结果");
        },
      });
    } catch (e) {
      setRunning(false);
      setError(e instanceof Error ? e.message : "发起失败");
    }
  };

  const openHistory = async (id: string) => {
    abortRef.current?.();
    reset();
    setViewing(id);
    const r = await api.get<ResearchReportInfo>(`/api/research/${id}`);
    setPlan(r.plan && r.plan.sub_questions ? r.plan : null);
    setReportText(r.report_md ?? "");
    setSources(r.sources ?? []);
    // 历史里存的是扁平结构（passed/completeness/citation_quality/logic），
    // 转换成组件需要的嵌套 scores 结构，避免 review.scores 为空导致渲染崩溃
    const rv = r.review as Record<string, unknown> | undefined;
    if (rv && "passed" in rv) {
      const scores = (rv.scores as Record<string, number>) ?? {
        completeness: rv.completeness as number,
        citation_quality: rv.citation_quality as number,
        logic: rv.logic as number,
      };
      setReview({
        passed: Boolean(rv.passed),
        scores,
        feedback: (rv.feedback as string) ?? "",
      });
    }
  };

  return (
    <div className="flex h-full">
      <div className="flex min-w-0 flex-1 flex-col">
        {/* 输入区 */}
        <div className="border-b border-zinc-800/80 px-6 py-4">
          <div className="mx-auto flex max-w-4xl gap-2">
            <input
              value={query}
              onChange={(e) => setQuery(e.target.value)}
              onKeyDown={(e) => e.key === "Enter" && start()}
              placeholder="输入研究主题，例如：2026 年国产大模型在企业落地的竞争格局分析"
              className="flex-1 rounded-xl border border-zinc-700/80 bg-zinc-900 px-4 py-2.5 text-sm text-zinc-200 placeholder:text-zinc-600 focus:border-indigo-500 focus:ring-1 focus:ring-indigo-500/50 focus:outline-none"
            />
            <Button onClick={start} disabled={query.trim().length < 4} loading={running}>
              <SendHorizonal size={15} /> 开始研究
            </Button>
          </div>
        </div>

        <div className="flex-1 overflow-y-auto px-6 py-5">
          <div className="mx-auto max-w-4xl space-y-4">
            {!plan && !running && !reportText && !error && !viewing && (
              <EmptyState
                icon={<Telescope size={30} />}
                title="深度研究 Agent"
                desc="规划子问题 → 并行搜索与阅读 → 证据交叉验证 → 生成带引用的结构化报告 → 评审修订"
              />
            )}

            {viewing && !plan && !running && !reportText && !error && (
              <div className="rounded-xl border border-zinc-800 bg-zinc-900/60 px-4 py-8 text-center text-sm text-zinc-500">
                这条历史研究没有可展示的报告内容（可能执行失败或未完成）。
              </div>
            )}

            {error && (
              <div className="rounded-xl border border-rose-500/40 bg-rose-500/10 px-4 py-3 text-sm text-rose-300">
                {error}
              </div>
            )}

            {/* 研究计划 */}
            {plan && (
              <div className="rounded-xl border border-zinc-800 bg-zinc-900/60 p-4">
                <div className="mb-2 flex items-center gap-2 text-sm font-medium text-zinc-200">
                  <ClipboardList size={15} className="text-indigo-400" /> 研究计划
                  <span className="text-xs font-normal text-zinc-500">{plan.topic}</span>
                </div>
                <div className="grid gap-2 md:grid-cols-2">
                  {plan.sub_questions.map((sq, i) => {
                    const worker = workers.find((w) => w.task_id === sq.id);
                    return (
                      <div key={sq.id} className="rounded-lg border border-zinc-800 bg-zinc-950/50 p-3">
                        <div className="flex items-start gap-2">
                          {worker?.status === "running" && (
                            <Loader2 size={14} className="mt-0.5 shrink-0 animate-spin text-indigo-400" />
                          )}
                          {worker?.status === "ok" && (
                            <CheckCircle2 size={14} className="mt-0.5 shrink-0 text-emerald-400" />
                          )}
                          {worker?.status === "failed" && (
                            <XCircle size={14} className="mt-0.5 shrink-0 text-rose-400" />
                          )}
                          {!worker && <Search size={14} className="mt-0.5 shrink-0 text-zinc-600" />}
                          <div className="min-w-0">
                            <div className="text-[13px] text-zinc-300">
                              {i + 1}. {sq.question}
                            </div>
                            {worker?.agent && (
                              <div className="mt-1 text-[10px] text-zinc-500">
                                {worker.agent}
                                {worker.evidence_count !== undefined && worker.status === "ok" && (
                                  <span> · 引用 {worker.evidence_count} 处来源</span>
                                )}
                              </div>
                            )}
                          </div>
                        </div>
                      </div>
                    );
                  })}
                </div>
              </div>
            )}

            {/* 评审结果：区分"实时修订中"与"历史已完成" */}
            {review && (
              <div
                className={
                  "flex items-center gap-3 rounded-xl border px-4 py-3 text-sm " +
                  (review.passed
                    ? "border-emerald-500/40 bg-emerald-500/10 text-emerald-300"
                    : "border-amber-500/40 bg-amber-500/10 text-amber-300")
                }
              >
                {review.passed ? (
                  <CheckCircle2 size={16} />
                ) : running ? (
                  <Loader2 size={16} className="animate-spin" />
                ) : (
                  <RefreshCw size={16} />
                )}
                <span>
                  评审员打分：完整性 {review.scores?.completeness ?? "-"}/5 · 引用规范{" "}
                  {review.scores?.citation_quality ?? "-"}/5 · 逻辑 {review.scores?.logic ?? "-"}/5
                  {!review.passed && (running ? " · 首版未达标，正在自动修订…" : " · 首版未达标，已自动修订（下方为修订后终稿）")}
                </span>
              </div>
            )}

            {/* 报告正文 */}
            {(reportText || running) && (
              <div className="rounded-xl border border-zinc-800 bg-zinc-900/60 p-5">
                {reportText ? (
                  <Markdown content={reportText} sources={sources} streaming={running} />
                ) : (
                  <div className="flex items-center gap-2 text-sm text-zinc-500">
                    <Loader2 size={15} className="animate-spin" /> 搜索员正在并行调研，稍后开始撰写报告…
                  </div>
                )}
              </div>
            )}
          </div>
        </div>
      </div>

      {/* 历史记录 */}
      <div className="flex w-72 shrink-0 flex-col border-l border-zinc-800/80">
        <div className="flex items-center gap-1.5 border-b border-zinc-800/80 px-4 py-3 text-xs font-medium tracking-wide text-zinc-400">
          <History size={13} /> 研究历史
        </div>
        <div className="flex-1 space-y-1 overflow-y-auto p-2">
          {history.length === 0 && (
            <div className="px-3 py-6 text-center text-xs text-zinc-600">暂无历史研究</div>
          )}
          {history.map((r) => (
            <button
              key={r.id}
              onClick={() => openHistory(r.id)}
              className={
                "w-full rounded-lg px-3 py-2.5 text-left hover:bg-zinc-800/60 " +
                (viewing === r.id ? "bg-zinc-800" : "")
              }
            >
              <div className="mb-1 line-clamp-2 text-[13px] text-zinc-300">{r.query}</div>
              <div className="flex items-center justify-between">
                <Badge tone={statusTone(r.status)}>{STATUS_LABEL[r.status] ?? r.status}</Badge>
                <span className="text-[10px] text-zinc-600">{formatTime(r.created_at)}</span>
              </div>
            </button>
          ))}
        </div>
      </div>
    </div>
  );
}
