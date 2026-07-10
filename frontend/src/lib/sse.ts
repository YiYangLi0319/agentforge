import { authHeaders, reportClientMetric } from "./api";
import type { AgentEvent } from "./types";

class SSEHttpError extends Error {
  constructor(readonly status: number) {
    super(`SSE HTTP ${status}`);
  }
}

/**
 * 基于 fetch 的 SSE 消费器（原生 EventSource 无法携带 Authorization 头）。
 * 返回中止函数；流结束后 resolve onDone。
 */
export function streamRunEvents(
  runId: string,
  handlers: {
    onEvent: (ev: AgentEvent) => void;
    onDone?: () => void;
    onError?: (err: unknown) => void;
    onReconnect?: (attempt: number) => void;
  },
  after = 0,
): () => void {
  const controller = new AbortController();

  (async () => {
    let lastSeq = after;
    let terminalSeen = false;
    const maxRetries = 4;
    for (let attempt = 0; attempt <= maxRetries && !controller.signal.aborted; attempt += 1) {
      try {
        const resp = await fetch(`/api/runs/${runId}/events?after=${lastSeq}`, {
          headers: authHeaders(),
          signal: controller.signal,
        });
        if (!resp.ok) throw new SSEHttpError(resp.status);
        if (!resp.body) throw new Error("SSE 响应缺少可读数据流");
        const reader = resp.body.getReader();
        const decoder = new TextDecoder();
        let buffer = "";
        for (;;) {
          const { done, value } = await reader.read();
          if (done) break;
          buffer = (buffer + decoder.decode(value, { stream: true })).replace(/\r\n/g, "\n");
          let idx: number;
          while ((idx = buffer.indexOf("\n\n")) >= 0) {
            const frame = buffer.slice(0, idx);
            buffer = buffer.slice(idx + 2);
            const dataLine = frame.split("\n").find((line) => line.startsWith("data:"));
            if (!dataLine) continue;
            const raw = dataLine.slice(5).trim();
            if (!raw || raw === "{}") continue;
            try {
              const event = JSON.parse(raw) as AgentEvent;
              const seq = event.seq ?? 0;
              if (seq > 0 && seq <= lastSeq) continue;
              if (seq > 0) lastSeq = seq;
              handlers.onEvent(event);
              if (["run_finished", "run_failed", "run_cancelled"].includes(event.type)) {
                terminalSeen = true;
              }
            } catch {
              /* 跳过无法解析的帧 */
            }
          }
        }
        if (terminalSeen) {
          handlers.onDone?.();
          return;
        }
        throw new Error("SSE 在任务结束前中断");
      } catch (err) {
        if (controller.signal.aborted) return;
        if (
          err instanceof SSEHttpError &&
          err.status < 500 &&
          err.status !== 408 &&
          err.status !== 429
        ) {
          handlers.onError?.(err);
          return;
        }
        if (attempt >= maxRetries) {
          handlers.onError?.(err);
          return;
        }
        handlers.onReconnect?.(attempt + 1);
        reportClientMetric("sse_reconnect"); // 供看板实时观测 SSE 断连恢复
        await new Promise((resolve) => setTimeout(resolve, 500 * 2 ** attempt));
      }
    }
  })();

  return () => controller.abort();
}
