const TOKEN_KEY = "agentforge_token";

export function getToken(): string | null {
  return localStorage.getItem(TOKEN_KEY);
}

export function setToken(token: string | null) {
  if (token) localStorage.setItem(TOKEN_KEY, token);
  else localStorage.removeItem(TOKEN_KEY);
}

export function authHeaders(): Record<string, string> {
  const token = getToken();
  return token ? { Authorization: `Bearer ${token}` } : {};
}

export class ApiError extends Error {
  status: number;
  constructor(status: number, message: string) {
    super(message);
    this.status = status;
  }
}

/** 后端错误 detail 可能是字符串、FastAPI 校验错误数组或对象，统一转成可读文字。 */
function normalizeDetail(detail: unknown): string | undefined {
  if (detail == null) return undefined;
  if (typeof detail === "string") return detail;
  if (Array.isArray(detail)) {
    const msgs = detail
      .map((d) => (d && typeof d === "object" && "msg" in d ? String((d as { msg: unknown }).msg) : String(d)))
      .filter(Boolean);
    return msgs.length ? msgs.join("；") : undefined;
  }
  if (typeof detail === "object" && "msg" in (detail as object)) {
    return String((detail as { msg: unknown }).msg);
  }
  try {
    return JSON.stringify(detail);
  } catch {
    return undefined;
  }
}

async function request<T>(path: string, options: RequestInit = {}): Promise<T> {
  const resp = await fetch(path, {
    ...options,
    headers: {
      ...(options.body && !(options.body instanceof FormData)
        ? { "Content-Type": "application/json" }
        : {}),
      ...authHeaders(),
      ...(options.headers ?? {}),
    },
  });
  if (resp.status === 401) {
    setToken(null);
    if (!location.pathname.startsWith("/login")) location.href = "/login";
    throw new ApiError(401, "登录已过期");
  }
  if (!resp.ok) {
    let detail = `HTTP ${resp.status}`;
    try {
      const body = await resp.json();
      detail = normalizeDetail(body.detail) ?? detail;
    } catch {
      /* ignore */
    }
    throw new ApiError(resp.status, detail);
  }
  if (resp.status === 204) return undefined as T;
  return (await resp.json()) as T;
}

async function download(path: string): Promise<{ blob: Blob; filename: string }> {
  const resp = await fetch(path, { headers: authHeaders() });
  if (resp.status === 401) {
    setToken(null);
    location.href = "/login";
    throw new ApiError(401, "登录已过期");
  }
  if (!resp.ok) {
    let detail = `HTTP ${resp.status}`;
    try {
      detail = normalizeDetail((await resp.json()).detail) ?? detail;
    } catch {
      /* ignore */
    }
    throw new ApiError(resp.status, detail);
  }
  const disposition = resp.headers.get("content-disposition") ?? "";
  const filename = disposition.match(/filename="?([^";]+)"?/i)?.[1] ?? "agentforge-export.md";
  return { blob: await resp.blob(), filename };
}

/** 上报前端观测事件（如 SSE 重连），best-effort，失败静默不影响主流程。 */
export function reportClientMetric(type: string): void {
  fetch("/api/dashboard/client-metric", {
    method: "POST",
    headers: { "Content-Type": "application/json", ...authHeaders() },
    body: JSON.stringify({ type }),
    keepalive: true,
  }).catch(() => undefined);
}

export const api = {
  get: <T>(path: string) => request<T>(path),
  post: <T>(path: string, body?: unknown) =>
    request<T>(path, { method: "POST", body: body === undefined ? undefined : JSON.stringify(body) }),
  postForm: <T>(path: string, form: FormData) => request<T>(path, { method: "POST", body: form }),
  patch: <T>(path: string, body: unknown) =>
    request<T>(path, { method: "PATCH", body: JSON.stringify(body) }),
  delete: <T>(path: string) => request<T>(path, { method: "DELETE" }),
  download,
};
