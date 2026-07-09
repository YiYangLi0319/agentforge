import { authHeaders } from "./api";
import type { AgentEvent } from "./types";

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
  },
  after = 0,
): () => void {
  const controller = new AbortController();

  (async () => {
    try {
      const resp = await fetch(`/api/runs/${runId}/events?after=${after}`, {
        headers: authHeaders(),
        signal: controller.signal,
      });
      if (!resp.ok || !resp.body) throw new Error(`SSE HTTP ${resp.status}`);
      const reader = resp.body.getReader();
      const decoder = new TextDecoder();
      let buffer = "";
      for (;;) {
        const { done, value } = await reader.read();
        if (done) break;
        buffer += decoder.decode(value, { stream: true });
        let idx: number;
        while ((idx = buffer.indexOf("\n\n")) >= 0) {
          const frame = buffer.slice(0, idx);
          buffer = buffer.slice(idx + 2);
          const dataLine = frame.split("\n").find((l) => l.startsWith("data:"));
          if (!dataLine) continue;
          const raw = dataLine.slice(5).trim();
          if (!raw || raw === "{}") continue;
          try {
            handlers.onEvent(JSON.parse(raw) as AgentEvent);
          } catch {
            /* 跳过无法解析的帧 */
          }
        }
      }
      handlers.onDone?.();
    } catch (err) {
      if (!controller.signal.aborted) handlers.onError?.(err);
    }
  })();

  return () => controller.abort();
}
