import {
  CheckCircle2,
  CircleStop,
  ClipboardList,
  Download,
  History,
  Loader2,
  RefreshCw,
  Search,
  SendHorizonal,
  Share2,
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

const PHASE_LABEL: Record<string, string> = {
  planning: "正在拆解研究问题",
  searching: "搜索员正在并行检索并阅读原文",
  synthesizing: "正在交叉验证证据与冲突",
  writing: "正在撰写研究报告",
  reviewing: "评审员正在核验引用与论证",
  revising: "正在按评审意见修订",
  completed: "研究已完成并通过质量门",
  needs_review: "已生成最佳版本，但未通过发布质量门",
  interrupted: "服务重启，任务已中断",
  cancelled: "任务已取消",
};

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
  const [shareUrl, setShareUrl] = useState("");
  const [runId, setRunId] = useState<string | null>(null);
  const [phase, setPhase] = useState("");
  const [progress, setProgress] = useState({ completed: 0, total: 0, revision: 0 });
  const [historyQuery, setHistoryQuery] = useState("");
  const [historyError, setHistoryError] = useState("");
  const [loadingHistory, setLoadingHistory] = useState(false);
  const abortRef = useRef<(() => void) | null>(null);
  // 请求代次：发起研究/切换历史时自增，异步回调据此丢弃过期结果，消除竞态。
  const genRef = useRef(0);

  const loadHistory = useCallback((search = "") => {
    const suffix = search.trim() ? `?q=${encodeURIComponent(search.trim())}` : "";
    api
      .get<ResearchReportInfo[]>(`/api/research${suffix}`)
      .then((rows) => {
        setHistory(rows);
        setHistoryError("");
      })
      .catch((reason) =>
        setHistoryError(reason instanceof Error ? reason.message : "加载研究历史失败"),
      );
  }, []);

  useEffect(() => {
    loadHistory();
    return () => abortRef.current?.();
  }, [loadHistory]);

  useEffect(() => {
    const timer = window.setTimeout(() => loadHistory(historyQuery), 250);
    return () => window.clearTimeout(timer);
  }, [historyQuery, loadHistory]);

  const reset = () => {
    setPlan(null);
    setWorkers([]);
    setReportText("");
    setReview(null);
    setSources([]);
    setError("");
    setViewing(null);
    setRunId(null);
    setPhase("");
    setProgress({ completed: 0, total: 0, revision: 0 });
  };

  const handleEvent = useCallback((ev: AgentEvent) => {
    switch (ev.type) {
      case "plan_created":
        if (ev.plan) setPlan(ev.plan);
        break;
      case "research_task_started":
        setWorkers((items) =>
          items.some((item) => item.task_id === ev.task_id)
            ? items
            : [
                ...items,
                { task_id: ev.task_id!, title: ev.title ?? "", agent: ev.agent, status: "running" },
              ],
        );
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
        break;
      case "research_phase_changed":
        setPhase(ev.phase ?? "");
        setProgress({
          completed: ev.completed_tasks ?? 0,
          total: ev.total_tasks ?? 0,
          revision: ev.revision ?? 0,
        });
        break;
      case "sources_updated":
        if (ev.sources) setSources(ev.sources);
        break;
      case "run_finished":
        if (ev.output?.report) setReportText(String(ev.output.report));
        if (ev.output?.sources) setSources(ev.output.sources);
        setRunning(false);
        setPhase(ev.output?.quality_passed === false ? "needs_review" : "completed");
        break;
      case "run_failed":
        setError(ev.error ?? "研究任务失败");
        setRunning(false);
        break;
      case "run_cancelled":
        setError("研究任务已取消");
        setRunning(false);
        setPhase("cancelled");
        break;
    }
  }, []);

  const start = async () => {
    const q = query.trim();
    if (q.length < 4 || running) return;
    const gen = ++genRef.current;
    abortRef.current?.();
    reset();
    setRunning(true);
    try {
      const resp = await api.post<{ run_id: string; report_id: string }>("/api/research", { query: q });
      if (gen !== genRef.current) return;
      setRunId(resp.run_id);
      setViewing(resp.report_id);
      abortRef.current = streamRunEvents(resp.run_id, {
        onEvent: (ev) => gen === genRef.current && handleEvent(ev),
        onDone: () => {
          if (gen !== genRef.current) return;
          setRunning(false);
          loadHistory();
        },
        onError: () => {
          if (gen !== genRef.current) return;
          setRunning(false);
          setError("事件流中断，可稍后在历史记录中查看结果");
        },
      });
    } catch (e) {
      if (gen !== genRef.current) return;
      setRunning(false);
      setError(e instanceof Error ? e.message : "发起失败");
    }
  };

  const doShare = async () => {
    if (!viewing) return;
    try {
      const r = await api.post<{ path: string }>(`/api/research/${viewing}/share`, {});
      const url = `${location.origin}${r.path}`;
      await navigator.clipboard.writeText(url);
      setShareUrl(url);
    } catch (e) {
      setError(e instanceof Error ? e.message : "分享失败");
    }
  };

  const openHistory = async (id: string) => {
    const gen = ++genRef.current;
    abortRef.current?.();
    reset();
    setViewing(id);
    setShareUrl("");
    setLoadingHistory(true);
    try {
      const r = await api.get<ResearchReportInfo>(`/api/research/${id}`);
      if (gen !== genRef.current) return;
      setRunId(r.run_id);
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
      if (["pending", "running", "awaiting_approval", "resuming"].includes(r.status)) {
        setRunning(true);
        abortRef.current = streamRunEvents(r.run_id, {
          onEvent: (ev) => gen === genRef.current && handleEvent(ev),
          onDone: () => {
            if (gen !== genRef.current) return;
            setRunning(false);
            loadHistory();
          },
          onError: () => {
            if (gen !== genRef.current) return;
            setRunning(false);
            setError("事件流恢复失败，请稍后重试");
          },
        });
      } else {
        setPhase(r.status);
      }
    } catch (reason) {
      if (gen !== genRef.current) return;
      setError(reason instanceof Error ? reason.message : "加载研究报告失败");
    } finally {
      if (gen === genRef.current) setLoadingHistory(false);
    }
  };

  const cancelResearch = async () => {
    if (!runId) return;
    try {
      await api.post(`/api/runs/${runId}/cancel`, {});
    } catch (reason) {
      setError(reason instanceof Error ? reason.message : "取消失败");
    }
  };

  const exportResearch = async () => {
    if (!viewing) return;
    try {
      const { blob, filename } = await api.download(`/api/research/${viewing}/export`);
      const url = URL.createObjectURL(blob);
      const link = document.createElement("a");
      link.href = url;
      link.download = filename;
      link.click();
      URL.revokeObjectURL(url);
    } catch (reason) {
      setError(reason instanceof Error ? reason.message : "导出失败");
    }
  };

  return (
    <div className="flex h-full">
      <div className="flex min-w-0 flex-1 flex-col">
        {/* 输入区 */}
        <div className="border-b border-zinc-800/80 px-3 py-3 sm:px-6 sm:py-4">
          <div className="mx-auto flex max-w-4xl flex-wrap gap-2">
            <label htmlFor="research-query" className="sr-only">
              研究主题
            </label>
            <input
              id="research-query"
              value={query}
              onChange={(e) => setQuery(e.target.value)}
              onKeyDown={(e) => e.key === "Enter" && start()}
              placeholder="输入研究主题，例如：2026 年国产大模型在企业落地的竞争格局分析"
              className="min-w-0 flex-1 rounded-xl border border-zinc-700/80 bg-zinc-900 px-4 py-2.5 text-sm text-zinc-200 placeholder:text-zinc-600 focus:border-indigo-500 focus:ring-1 focus:ring-indigo-500/50 focus:outline-none"
            />
            {running ? (
              <Button variant="danger" onClick={cancelResearch}>
                <CircleStop size={15} /> 停止
              </Button>
            ) : (
              <Button onClick={start} disabled={query.trim().length < 4}>
                <SendHorizonal size={15} /> 开始研究
              </Button>
            )}
            <select
              value={viewing ?? ""}
              onChange={(event) => event.target.value && openHistory(event.target.value)}
              aria-label="选择历史研究"
              className="w-full rounded-lg border border-zinc-800 bg-zinc-900 px-3 py-2 text-xs text-zinc-300 lg:hidden"
            >
              <option value="">选择历史研究…</option>
              {history.map((item) => (
                <option key={item.id} value={item.id}>
                  {item.query}
                </option>
              ))}
            </select>
          </div>
        </div>

        <div className="flex-1 overflow-y-auto px-3 py-4 sm:px-6 sm:py-5">
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
              <div role="alert" className="rounded-xl border border-rose-500/40 bg-rose-500/10 px-4 py-3 text-sm text-rose-300">
                {error}
              </div>
            )}

            {(phase || loadingHistory) && (
              <div
                aria-live="polite"
                className="flex items-center gap-2 rounded-xl border border-indigo-500/25 bg-indigo-500/8 px-4 py-3 text-sm text-indigo-200"
              >
                {(running || loadingHistory) && <Loader2 size={15} className="animate-spin" />}
                <span>{loadingHistory ? "正在恢复研究现场…" : (PHASE_LABEL[phase] ?? phase)}</span>
                {phase === "searching" && progress.total > 0 && (
                  <Badge tone="indigo">
                    {progress.completed}/{progress.total}
                  </Badge>
                )}
                {phase === "revising" && progress.revision > 0 && (
                  <Badge tone="indigo">第 {progress.revision} 轮</Badge>
                )}
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

            {/* 分享（仅查看历史且有报告时） */}
            {viewing && reportText && !running && (
              <div className="flex items-center gap-2">
                <Button size="sm" variant="outline" onClick={exportResearch}>
                  <Download size={13} /> 导出 Markdown
                </Button>
                {(phase === "completed" || phase === "succeeded") && (
                  <Button size="sm" variant="outline" onClick={doShare}>
                    <Share2 size={13} /> 生成公开分享链接
                  </Button>
                )}
                {shareUrl && (
                  <a
                    href={shareUrl}
                    target="_blank"
                    rel="noreferrer"
                    className="truncate rounded-lg bg-emerald-500/10 px-2.5 py-1.5 text-[11px] text-emerald-300"
                  >
                    已复制到剪贴板：{shareUrl}
                  </a>
                )}
              </div>
            )}

            {/* 报告正文 */}
            {(reportText || running) && (
              <div className="rounded-xl border border-zinc-800 bg-zinc-900/60 p-5">
                {reportText ? (
                  <Markdown content={reportText} sources={sources} streaming={running} />
                ) : (
                  <div className="flex items-center gap-2 text-sm text-zinc-500">
                    <Loader2 size={15} className="animate-spin" /> {PHASE_LABEL[phase] ?? "研究任务正在执行…"}
                  </div>
                )}
              </div>
            )}
          </div>
        </div>
      </div>

      {/* 历史记录 */}
      <div className="hidden w-72 shrink-0 flex-col border-l border-zinc-800/80 lg:flex">
        <div className="flex items-center gap-1.5 border-b border-zinc-800/80 px-4 py-3 text-xs font-medium tracking-wide text-zinc-400">
          <History size={13} /> 研究历史
        </div>
        <label className="relative mx-2 mt-2 block">
          <span className="sr-only">搜索研究历史</span>
          <Search size={13} className="pointer-events-none absolute top-1/2 left-2.5 -translate-y-1/2 text-zinc-600" />
          <input
            value={historyQuery}
            onChange={(event) => setHistoryQuery(event.target.value)}
            placeholder="搜索研究主题…"
            className="w-full rounded-lg border border-zinc-800 bg-zinc-900 py-2 pr-2 pl-8 text-xs text-zinc-300 placeholder:text-zinc-600 focus:border-indigo-500 focus:outline-none"
          />
        </label>
        {historyError && <div className="px-3 py-2 text-xs text-rose-400">{historyError}</div>}
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
