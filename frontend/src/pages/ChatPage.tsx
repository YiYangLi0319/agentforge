import {
  Bot,
  CircleStop,
  Download,
  MessageSquarePlus,
  MessagesSquare,
  Pencil,
  RotateCcw,
  Search,
  SendHorizonal,
  ShieldQuestion,
  Sparkles,
  ThumbsDown,
  ThumbsUp,
  Trash2,
  Users,
} from "lucide-react";
import { useCallback, useEffect, useRef, useState } from "react";

import AgentTimeline, { reduceTimeline, type TimelineItem } from "../components/AgentTimeline";
import Markdown from "../components/Markdown";
import { Badge, Button, EmptyState, Modal, formatCost } from "../components/ui";
import { api } from "../lib/api";
import { streamRunEvents } from "../lib/sse";
import type {
  AgentEvent,
  ChatMessageInfo,
  ChatSessionInfo,
  CustomAgentInfo,
  KnowledgeBaseInfo,
  Source,
} from "../lib/types";

interface PendingApproval {
  tool_call_id: string;
  tool: string;
  arguments: Record<string, unknown>;
}

interface ActiveRun {
  id: string;
  status: string;
}

export default function ChatPage() {
  const [sessions, setSessions] = useState<ChatSessionInfo[]>([]);
  const [activeId, setActiveId] = useState<string | null>(null);
  const [messages, setMessages] = useState<ChatMessageInfo[]>([]);
  const [input, setInput] = useState("");
  const [running, setRunning] = useState(false);
  const [runId, setRunId] = useState<string | null>(null);
  const [streamText, setStreamText] = useState("");
  const [streamSources, setStreamSources] = useState<Source[]>([]);
  const [timeline, setTimeline] = useState<TimelineItem[]>([]);
  const [approvals, setApprovals] = useState<PendingApproval[]>([]);
  const [lastUsage, setLastUsage] = useState<{ tokens: number; cost: number } | null>(null);
  const [showCreate, setShowCreate] = useState(false);
  const [kbs, setKbs] = useState<KnowledgeBaseInfo[]>([]);
  const [newKbIds, setNewKbIds] = useState<string[]>([]);
  const [newAgentType, setNewAgentType] = useState<"assistant" | "team" | "custom">("assistant");
  const [customAgents, setCustomAgents] = useState<CustomAgentInfo[]>([]);
  const [newCustomAgentId, setNewCustomAgentId] = useState<string>("");
  const [sessionQuery, setSessionQuery] = useState("");
  const [editingId, setEditingId] = useState<string | null>(null);
  const [editingTitle, setEditingTitle] = useState("");
  const [pageError, setPageError] = useState("");
  const [activeRun, setActiveRun] = useState<ActiveRun | null>(null);
  const [resumableRun, setResumableRun] = useState<{ id: string } | null>(null);

  const abortRef = useRef<(() => void) | null>(null);
  const bottomRef = useRef<HTMLDivElement>(null);
  const runIdRef = useRef<string | null>(null);
  // 请求代次：切换会话/发送时自增，异步回调据此丢弃过期结果，消除竞态。
  const genRef = useRef(0);
  const activeSession = sessions.find((s) => s.id === activeId);

  const loadSessions = useCallback(async (search = "") => {
    const suffix = search.trim() ? `?q=${encodeURIComponent(search.trim())}` : "";
    const list = await api.get<ChatSessionInfo[]>(`/api/chat/sessions${suffix}`);
    setSessions(list);
    return list;
  }, []);

  useEffect(() => {
    loadSessions().then((list) => {
      if (list.length > 0) setActiveId((prev) => prev ?? list[0].id);
    });
    api.get<KnowledgeBaseInfo[]>("/api/kb").then(setKbs).catch(() => undefined);
    api.get<CustomAgentInfo[]>("/api/agents").then(setCustomAgents).catch(() => undefined);
    return () => abortRef.current?.();
  }, [loadSessions]);

  useEffect(() => {
    const timer = window.setTimeout(() => {
      loadSessions(sessionQuery).catch((error) =>
        setPageError(error instanceof Error ? error.message : "加载会话失败"),
      );
    }, 250);
    return () => window.clearTimeout(timer);
  }, [loadSessions, sessionQuery]);

  useEffect(() => {
    if (!activeId) return;
    const gen = ++genRef.current;
    abortRef.current?.();
    setStreamText("");
    setTimeline([]);
    setApprovals([]);
    setRunning(false);
    setActiveRun(null);
    setResumableRun(null);
    setRunId(null);
    runIdRef.current = null;
    api
      .get<{
        messages: ChatMessageInfo[];
        active_run: ActiveRun | null;
        resumable_run: { id: string } | null;
      }>(`/api/chat/sessions/${activeId}`)
      .then((d) => {
        if (gen !== genRef.current) return;
        setMessages(d.messages);
        setActiveRun(d.active_run);
        setResumableRun(d.resumable_run);
        setPageError("");
      })
      .catch((error) => {
        if (gen !== genRef.current) return;
        setMessages([]);
        setPageError(error instanceof Error ? error.message : "加载消息失败");
      });
  }, [activeId]);

  useEffect(() => {
    bottomRef.current?.scrollIntoView({ behavior: "smooth" });
  }, [messages, streamText, timeline.length]);

  const handleEvent = useCallback((ev: AgentEvent) => {
    setTimeline((items) => reduceTimeline(items, ev));
    switch (ev.type) {
      case "llm_delta":
        if (ev.channel === "answer" && ev.text) setStreamText((t) => t + ev.text);
        break;
      case "assistant_message":
        if (ev.final && ev.content) setStreamText(ev.content);
        break;
      case "sources_updated":
        if (ev.sources) setStreamSources(ev.sources);
        break;
      case "approval_required":
        setApprovals((a) => [
          ...a,
          { tool_call_id: ev.tool_call_id!, tool: ev.tool!, arguments: ev.arguments ?? {} },
        ]);
        break;
      case "approval_decided":
        setApprovals((a) => a.filter((x) => x.tool_call_id !== ev.tool_call_id));
        break;
      case "run_finished": {
        const text = ev.output?.text ?? "";
        const sources = ev.output?.sources ?? [];
        setMessages((msgs) =>
          runIdRef.current && msgs.some((message) => message.run_id === runIdRef.current)
            ? msgs
            : [
                ...msgs,
                {
                  id: `local-${Date.now()}`,
                  role: "assistant",
                  content: String(text),
                  sources,
                  run_id: runIdRef.current ?? undefined,
                  created_at: new Date().toISOString(),
                },
              ],
        );
        setStreamText("");
        setStreamSources([]);
        setRunning(false);
        if (ev.usage) {
          setLastUsage({
            tokens: ev.usage.prompt_tokens + ev.usage.completion_tokens,
            cost: ev.cost ?? 0,
          });
        }
        break;
      }
      case "run_failed":
        setMessages((msgs) => [
          ...msgs,
          {
            id: `err-${Date.now()}`,
            role: "assistant",
            content: `⚠️ 运行失败：${ev.error?.split("\n")[0] ?? "未知错误"}`,
            sources: [],
            created_at: new Date().toISOString(),
          },
        ]);
        setStreamText("");
        setRunning(false);
        break;
      case "run_cancelled":
        setStreamText("");
        setRunning(false);
        break;
    }
  }, []);

  useEffect(() => {
    if (!activeRun) return;
    const gen = genRef.current;
    setRunId(activeRun.id);
    runIdRef.current = activeRun.id;
    setRunning(true);
    abortRef.current = streamRunEvents(activeRun.id, {
      onEvent: handleEvent,
      onError: () => {
        if (gen !== genRef.current) return;
        setRunning(false);
        setPageError("事件流恢复失败，请刷新会话查看最终结果");
      },
    });
    return () => abortRef.current?.();
  }, [activeRun, handleEvent]);

  const send = async () => {
    const content = input.trim();
    if (!content || running || !activeId) return;
    const gen = genRef.current;
    const optimisticId = `u-${Date.now()}`;
    setInput("");
    setResumableRun(null);
    setMessages((m) => [
      ...m,
      {
        id: optimisticId,
        role: "user",
        content,
        sources: [],
        created_at: new Date().toISOString(),
      },
    ]);
    setTimeline([]);
    setApprovals([]);
    setLastUsage(null);
    setRunning(true);
    try {
      const resp = await api.post<{ run_id: string }>(`/api/chat/sessions/${activeId}/messages`, {
        content,
      });
      if (gen !== genRef.current) return; // 已切换会话，放弃在当前视图订阅
      setRunId(resp.run_id);
      runIdRef.current = resp.run_id;
      abortRef.current = streamRunEvents(resp.run_id, {
        onEvent: handleEvent,
        onError: () => setRunning(false),
      });
      loadSessions();
    } catch (e) {
      if (gen !== genRef.current) return;
      setRunning(false);
      setMessages((m) => [
        ...m.filter((message) => message.id !== optimisticId),
        {
          id: `err-${Date.now()}`,
          role: "assistant",
          content: `⚠️ ${e instanceof Error ? e.message : "发送失败"}`,
          sources: [],
          created_at: new Date().toISOString(),
        },
      ]);
    }
  };

  const resumeRun = async () => {
    if (!resumableRun) return;
    const gen = genRef.current;
    try {
      const resp = await api.post<{ run_id: string }>(`/api/runs/${resumableRun.id}/resume`, {});
      if (gen !== genRef.current) return;
      setResumableRun(null);
      setActiveRun({ id: resp.run_id, status: "running" });
    } catch (error) {
      if (gen !== genRef.current) return;
      setPageError(error instanceof Error ? error.message : "恢复失败");
    }
  };

  const decide = async (tcId: string, approved: boolean) => {
    if (!runId) return;
    try {
      await api.post(`/api/runs/${runId}/approval`, { tool_call_id: tcId, approved });
    } catch {
      setApprovals((a) => a.filter((x) => x.tool_call_id !== tcId));
    }
  };

  const createSession = async () => {
    if (newAgentType === "custom" && !newCustomAgentId) return;
    const s = await api.post<ChatSessionInfo>("/api/chat/sessions", {
      agent_type: newAgentType,
      custom_agent_id: newAgentType === "custom" ? newCustomAgentId : undefined,
      kb_ids: newKbIds,
    });
    setShowCreate(false);
    setNewKbIds([]);
    setSessionQuery("");
    await loadSessions("");
    setActiveId(s.id);
  };

  const removeSession = async (id: string) => {
    const target = sessions.find((session) => session.id === id);
    if (!window.confirm(`确认删除会话“${target?.title ?? "未命名会话"}”？此操作不可撤销。`)) return;
    try {
      await api.delete(`/api/chat/sessions/${id}`);
      const list = await loadSessions(sessionQuery);
      if (activeId === id) setActiveId(list[0]?.id ?? null);
    } catch (error) {
      setPageError(error instanceof Error ? error.message : "删除失败");
    }
  };

  const saveTitle = async (id: string) => {
    const title = editingTitle.trim();
    setEditingId(null);
    if (!title) return;
    try {
      await api.patch(`/api/chat/sessions/${id}`, { title });
      await loadSessions(sessionQuery);
    } catch (error) {
      setPageError(error instanceof Error ? error.message : "重命名失败");
    }
  };

  const exportSession = async (id: string) => {
    try {
      const { blob, filename } = await api.download(`/api/chat/sessions/${id}/export`);
      const url = URL.createObjectURL(blob);
      const link = document.createElement("a");
      link.href = url;
      link.download = filename;
      link.click();
      URL.revokeObjectURL(url);
    } catch (error) {
      setPageError(error instanceof Error ? error.message : "导出失败");
    }
  };

  return (
    <div className="flex h-full">
      {/* 会话列表 */}
      <div className="hidden w-60 shrink-0 flex-col border-r border-zinc-800/80 md:flex">
        <div className="space-y-2 p-3">
          <Button onClick={() => setShowCreate(true)} className="w-full" size="sm">
            <MessageSquarePlus size={14} /> 新建对话
          </Button>
          <label className="relative block">
            <span className="sr-only">搜索会话</span>
            <Search
              size={13}
              className="pointer-events-none absolute top-1/2 left-2.5 -translate-y-1/2 text-zinc-600"
            />
            <input
              value={sessionQuery}
              onChange={(event) => setSessionQuery(event.target.value)}
              placeholder="搜索会话…"
              className="w-full rounded-lg border border-zinc-800 bg-zinc-900 py-2 pr-2 pl-8 text-xs text-zinc-300 placeholder:text-zinc-600 focus:border-indigo-500 focus:outline-none"
            />
          </label>
        </div>
        <div className="flex-1 space-y-0.5 overflow-y-auto px-2 pb-2">
          {sessions.map((s) => (
            <div
              key={s.id}
              className={
                "group flex items-center gap-1 rounded-lg px-1.5 py-1 text-[13px] " +
                (s.id === activeId ? "bg-zinc-800 text-zinc-100" : "text-zinc-400 hover:bg-zinc-800/50")
              }
            >
              {editingId === s.id ? (
                <div className="flex min-w-0 flex-1 items-center gap-2 px-1 py-1">
                  {s.agent_type === "team" ? (
                    <Users size={13} className="shrink-0 text-violet-400" />
                  ) : (
                    <Bot size={13} className="shrink-0 text-indigo-400" />
                  )}
                  <input
                    autoFocus
                    value={editingTitle}
                    onChange={(event) => setEditingTitle(event.target.value)}
                    onBlur={() => saveTitle(s.id)}
                    onKeyDown={(event) => {
                      if (event.key === "Enter") void saveTitle(s.id);
                      if (event.key === "Escape") setEditingId(null);
                    }}
                    className="min-w-0 flex-1 rounded border border-indigo-500 bg-zinc-950 px-1.5 py-0.5 text-xs outline-none"
                    aria-label="会话标题"
                  />
                </div>
              ) : (
                <button
                  type="button"
                  onClick={() => setActiveId(s.id)}
                  className="flex min-w-0 flex-1 items-center gap-2 rounded-md px-1 py-1 text-left"
                >
                  {s.agent_type === "team" ? (
                    <Users size={13} className="shrink-0 text-violet-400" />
                  ) : (
                    <Bot size={13} className="shrink-0 text-indigo-400" />
                  )}
                  <span className="flex-1 truncate">{s.title}</span>
                  {s.kb_ids.length > 0 && <Badge tone="green">库</Badge>}
                </button>
              )}
              <button
                type="button"
                onClick={() => {
                  setEditingId(s.id);
                  setEditingTitle(s.title);
                }}
                aria-label={`重命名 ${s.title}`}
                className="rounded p-1 text-zinc-600 opacity-0 hover:text-indigo-300 focus:opacity-100 group-hover:opacity-100"
              >
                <Pencil size={12} />
              </button>
              <button
                type="button"
                onClick={() => exportSession(s.id)}
                aria-label={`导出 ${s.title}`}
                className="rounded p-1 text-zinc-600 opacity-0 hover:text-emerald-300 focus:opacity-100 group-hover:opacity-100"
              >
                <Download size={12} />
              </button>
              <button
                type="button"
                onClick={() => removeSession(s.id)}
                aria-label={`删除 ${s.title}`}
                className="rounded p-1 text-zinc-600 opacity-0 hover:text-rose-400 focus:opacity-100 group-hover:opacity-100"
              >
                <Trash2 size={12} />
              </button>
            </div>
          ))}
          {sessions.length === 0 && (
            <div className="px-3 py-6 text-center text-xs text-zinc-600">
              {sessionQuery ? "没有匹配的会话" : "暂无会话"}
            </div>
          )}
        </div>
      </div>

      {/* 消息区 */}
      <div className="flex min-w-0 flex-1 flex-col">
        <div className="flex items-center gap-2 border-b border-zinc-800/80 p-2 md:hidden">
          <select
            value={activeId ?? ""}
            onChange={(event) => setActiveId(event.target.value || null)}
            aria-label="选择会话"
            className="min-w-0 flex-1 rounded-lg border border-zinc-800 bg-zinc-900 px-3 py-2 text-xs text-zinc-300"
          >
            <option value="">选择会话</option>
            {sessions.map((session) => (
              <option key={session.id} value={session.id}>
                {session.title}
              </option>
            ))}
          </select>
          <Button size="sm" onClick={() => setShowCreate(true)} aria-label="新建对话">
            <MessageSquarePlus size={14} />
          </Button>
        </div>
        {pageError && (
          <div role="alert" className="border-b border-rose-500/30 bg-rose-500/10 px-4 py-2 text-xs text-rose-300">
            {pageError}
          </div>
        )}
        {activeId ? (
          <>
            {resumableRun && !running && !activeRun && (
              <div className="flex items-center justify-between gap-2 border-b border-amber-500/30 bg-amber-500/10 px-4 py-2 text-xs text-amber-200">
                <span>上一轮对话因服务重启被中断，可从断点继续。</span>
                <Button size="sm" variant="outline" onClick={resumeRun}>
                  <RotateCcw size={13} /> 恢复
                </Button>
              </div>
            )}
            <div className="flex-1 overflow-y-auto px-3 py-4 sm:px-6 sm:py-5">
              <div className="mx-auto max-w-3xl space-y-5">
                {messages.map((m) => (
                  <MessageBubble key={m.id} msg={m} />
                ))}
                {streamText && (
                  <div className="rounded-2xl rounded-tl-sm border border-zinc-800 bg-zinc-900/70 px-4 py-3">
                    <Markdown content={streamText} sources={streamSources} streaming />
                  </div>
                )}
                {running && !streamText && (
                  <div className="flex items-center gap-2 text-sm text-zinc-500">
                    <span className="inline-flex h-2 w-2 animate-pulse rounded-full bg-indigo-400" />
                    Agent 正在思考与调用工具…
                  </div>
                )}
                <div ref={bottomRef} />
              </div>
            </div>

            <div className="border-t border-zinc-800/80 px-3 py-3 sm:px-6 sm:py-4">
              <div className="mx-auto flex max-w-3xl items-end gap-2">
                <label htmlFor="chat-input" className="sr-only">
                  输入消息
                </label>
                <textarea
                  id="chat-input"
                  value={input}
                  onChange={(e) => setInput(e.target.value)}
                  onKeyDown={(e) => {
                    if (e.key === "Enter" && !e.shiftKey) {
                      e.preventDefault();
                      send();
                    }
                  }}
                  rows={2}
                  placeholder={
                    activeSession?.kb_ids.length
                      ? "问点什么…（已绑定知识库，回答会带引用溯源）"
                      : "问点什么…（Enter 发送，Shift+Enter 换行）"
                  }
                  className="max-h-40 flex-1 resize-none rounded-xl border border-zinc-700/80 bg-zinc-900 px-4 py-3 text-sm text-zinc-200 placeholder:text-zinc-600 focus:border-indigo-500 focus:ring-1 focus:ring-indigo-500/50 focus:outline-none"
                />
                {running ? (
                  <Button variant="danger" onClick={() => runId && api.post(`/api/runs/${runId}/cancel`).catch(() => undefined)}>
                    <CircleStop size={15} /> 停止
                  </Button>
                ) : (
                  <Button onClick={send} disabled={!input.trim()}>
                    <SendHorizonal size={15} /> 发送
                  </Button>
                )}
              </div>
            </div>
          </>
        ) : (
          <EmptyState
            icon={<MessagesSquare size={28} />}
            title="创建一个对话开始"
            desc="支持普通助手与多 Agent 团队两种模式，绑定知识库后回答自动带引用溯源"
          />
        )}
      </div>

      {/* 执行过程面板 */}
      <div className="hidden w-80 shrink-0 flex-col border-l border-zinc-800/80 xl:flex">
        <div className="border-b border-zinc-800/80 px-4 py-3 text-xs font-medium tracking-wide text-zinc-400">
          执行过程
          {activeSession?.agent_type === "team" && (
            <Badge tone="indigo">多 Agent 团队</Badge>
          )}
        </div>
        <div className="flex-1 overflow-y-auto p-3">
          {approvals.map((a) => (
            <div key={a.tool_call_id} className="mb-2 rounded-xl border border-amber-500/40 bg-amber-500/10 p-3">
              <div className="mb-1.5 flex items-center gap-1.5 text-xs font-medium text-amber-300">
                <ShieldQuestion size={14} /> 高危操作待审批
              </div>
              <div className="mb-1 font-mono text-[11px] text-zinc-300">{a.tool}</div>
              <pre className="mb-2 max-h-32 overflow-auto rounded bg-zinc-950/70 p-2 font-mono text-[11px] whitespace-pre-wrap text-zinc-400">
                {JSON.stringify(a.arguments, null, 2)}
              </pre>
              <div className="flex gap-2">
                <Button size="sm" onClick={() => decide(a.tool_call_id, true)}>
                  批准执行
                </Button>
                <Button size="sm" variant="outline" onClick={() => decide(a.tool_call_id, false)}>
                  拒绝
                </Button>
              </div>
            </div>
          ))}
          <AgentTimeline items={timeline} running={running} />
        </div>
        {lastUsage && (
          <div className="border-t border-zinc-800/80 px-4 py-2.5 text-[11px] text-zinc-500">
            本轮消耗 <span className="font-mono text-zinc-300">{lastUsage.tokens.toLocaleString()}</span> tokens ·
            成本约 <span className="font-mono text-zinc-300">{formatCost(lastUsage.cost)}</span>
          </div>
        )}
      </div>

      {/* 新建对话弹窗 */}
      <Modal open={showCreate} onClose={() => setShowCreate(false)} labelledBy="create-chat-title" className="mx-3">
        <h3 id="create-chat-title" className="mb-4 text-sm font-semibold text-zinc-100">
          新建对话
        </h3>
        <div>
            <div className="mb-3">
              <div className="mb-1.5 text-xs text-zinc-500">Agent 模式</div>
              <div className="grid grid-cols-3 gap-2">
                {(
                  [
                    ["assistant", "智能助手", "单 Agent · ReAct", Bot],
                    ["team", "专家团队", "多 Agent 协作", Users],
                    ["custom", "专属 Agent", "你自建的 Agent", Sparkles],
                  ] as const
                ).map(([value, label, desc, Icon]) => (
                  <button
                    key={value}
                    onClick={() => setNewAgentType(value)}
                    className={
                      "rounded-xl border p-3 text-left transition-colors " +
                      (newAgentType === value
                        ? "border-indigo-500 bg-indigo-500/10"
                        : "border-zinc-700/80 hover:border-zinc-600")
                    }
                  >
                    <Icon size={15} className={newAgentType === value ? "text-indigo-400" : "text-zinc-500"} />
                    <div className="mt-1 text-[12px] font-medium text-zinc-200">{label}</div>
                    <div className="text-[10px] text-zinc-500">{desc}</div>
                  </button>
                ))}
              </div>
              {newAgentType === "custom" && (
                <div className="mt-2">
                  {customAgents.length === 0 ? (
                    <div className="rounded-lg bg-zinc-800/60 px-3 py-2 text-xs text-zinc-500">
                      还没有自定义 Agent，先到「自定义 Agent」页面创建
                    </div>
                  ) : (
                    <select
                      value={newCustomAgentId}
                      onChange={(e) => setNewCustomAgentId(e.target.value)}
                      className="w-full rounded-lg border border-zinc-700/80 bg-zinc-900 px-3 py-2 text-sm text-zinc-200 focus:outline-none"
                    >
                      <option value="">选择一个自定义 Agent…</option>
                      {customAgents.map((a) => (
                        <option key={a.id} value={a.id}>
                          {a.name}
                        </option>
                      ))}
                    </select>
                  )}
                </div>
              )}
            </div>
            <div className="mb-4">
              <div className="mb-1.5 text-xs text-zinc-500">绑定知识库（可多选，绑定后回答带引用溯源）</div>
              {kbs.length === 0 ? (
                <div className="rounded-lg bg-zinc-800/60 px-3 py-2 text-xs text-zinc-500">
                  暂无知识库，可先到「知识库」页面创建并上传文档
                </div>
              ) : (
                <div className="max-h-32 space-y-1 overflow-y-auto">
                  {kbs.map((kb) => (
                    <label key={kb.id} className="flex cursor-pointer items-center gap-2 rounded-lg px-2 py-1.5 text-[13px] text-zinc-300 hover:bg-zinc-800/60">
                      <input
                        type="checkbox"
                        checked={newKbIds.includes(kb.id)}
                        onChange={(e) =>
                          setNewKbIds((ids) =>
                            e.target.checked ? [...ids, kb.id] : ids.filter((x) => x !== kb.id),
                          )
                        }
                        className="accent-indigo-500"
                      />
                      {kb.name}
                      <span className="text-[10px] text-zinc-600">{kb.doc_count} 文档</span>
                    </label>
                  ))}
                </div>
              )}
            </div>
            <div className="flex justify-end gap-2">
              <Button variant="ghost" onClick={() => setShowCreate(false)}>
                取消
              </Button>
              <Button onClick={createSession}>创建</Button>
            </div>
        </div>
      </Modal>
    </div>
  );
}

function MessageBubble({ msg }: { msg: ChatMessageInfo }) {
  if (msg.role === "user") {
    return (
      <div className="flex justify-end">
        <div className="max-w-[85%] rounded-2xl rounded-tr-sm bg-indigo-600/90 px-4 py-2.5 text-[15px] leading-7 whitespace-pre-wrap text-white">
          {msg.content}
        </div>
      </div>
    );
  }
  return (
    <div className="rounded-2xl rounded-tl-sm border border-zinc-800 bg-zinc-900/70 px-4 py-3">
      <Markdown content={msg.content} sources={msg.sources} />
      {msg.sources.length > 0 && (
        <div className="mt-3 border-t border-zinc-800/80 pt-2.5">
          <div className="mb-1.5 text-[11px] text-zinc-500">引用来源</div>
          <div className="flex flex-wrap gap-1.5">
            {msg.sources.map((s) => (
              <a
                key={s.id}
                href={s.url || undefined}
                target={s.url ? "_blank" : undefined}
                rel="noreferrer"
                className="inline-flex max-w-64 items-center gap-1 rounded-lg border border-zinc-700/70 bg-zinc-800/60 px-2 py-1 text-[11px] text-zinc-300 hover:border-indigo-500/50"
                title={s.snippet}
              >
                <span className="font-mono text-indigo-400">[{s.id}]</span>
                <span className="truncate">{s.title || s.filename}</span>
              </a>
            ))}
          </div>
        </div>
      )}
      {msg.run_id && <FeedbackButtons runId={msg.run_id} />}
    </div>
  );
}

function FeedbackButtons({ runId }: { runId: string }) {
  const [rating, setRating] = useState<"up" | "down" | null>(null);
  const submit = async (r: "up" | "down") => {
    setRating(r);
    try {
      await api.post("/api/feedback", { run_id: runId, rating: r });
    } catch {
      /* ignore */
    }
  };
  return (
    <div className="mt-2 flex items-center gap-1.5">
      <button
        onClick={() => submit("up")}
        className={"rounded p-1 " + (rating === "up" ? "text-emerald-400" : "text-zinc-600 hover:text-zinc-400")}
        title="有帮助"
      >
        <ThumbsUp size={13} />
      </button>
      <button
        onClick={() => submit("down")}
        className={"rounded p-1 " + (rating === "down" ? "text-rose-400" : "text-zinc-600 hover:text-zinc-400")}
        title="没帮助"
      >
        <ThumbsDown size={13} />
      </button>
      {rating && <span className="text-[10px] text-zinc-600">感谢反馈</span>}
    </div>
  );
}
