import { Boxes } from "lucide-react";
import { useEffect, useState } from "react";
import { useNavigate } from "react-router-dom";

import { Button, Input } from "../components/ui";
import { api } from "../lib/api";
import { useAuth } from "../stores/auth";

export default function LoginPage() {
  const [mode, setMode] = useState<"login" | "register">("login");
  const [username, setUsername] = useState("");
  const [password, setPassword] = useState("");
  const [inviteCode, setInviteCode] = useState("");
  const [requiresCode, setRequiresCode] = useState(false);
  const [error, setError] = useState("");
  const [loading, setLoading] = useState(false);
  const { login, register } = useAuth();
  const navigate = useNavigate();

  useEffect(() => {
    api
      .get<{ registration_requires_code?: boolean }>("/api/meta")
      .then((m) => setRequiresCode(Boolean(m.registration_requires_code)))
      .catch(() => undefined);
  }, []);

  const submit = async () => {
    if (!username || !password) return;
    setLoading(true);
    setError("");
    try {
      await (mode === "login" ? login(username, password) : register(username, password, inviteCode));
      navigate("/chat");
    } catch (e) {
      setError(e instanceof Error ? e.message : "操作失败");
    } finally {
      setLoading(false);
    }
  };

  return (
    <div className="flex h-full items-center justify-center bg-[radial-gradient(ellipse_at_top,rgba(99,102,241,0.12),transparent_60%)]">
      <div className="w-96 rounded-2xl border border-zinc-800 bg-zinc-900/70 p-8 shadow-2xl backdrop-blur">
        <div className="mb-6 flex flex-col items-center gap-3">
          <div className="flex h-14 w-14 items-center justify-center rounded-2xl bg-gradient-to-br from-indigo-500 to-violet-600 shadow-lg shadow-indigo-950">
            <Boxes size={28} className="text-white" />
          </div>
          <div className="text-center">
            <h1 className="text-xl font-bold text-zinc-100">AgentForge</h1>
            <p className="mt-1 text-xs text-zinc-500">自研 Agent 引擎 · Agentic RAG · 深度研究</p>
          </div>
        </div>

        <div className="mb-4 grid grid-cols-2 rounded-lg bg-zinc-800/70 p-1 text-sm">
          {(["login", "register"] as const).map((m) => (
            <button
              key={m}
              onClick={() => setMode(m)}
              className={
                "rounded-md py-1.5 transition-colors " +
                (mode === m ? "bg-indigo-600 font-medium text-white" : "text-zinc-400 hover:text-zinc-200")
              }
            >
              {m === "login" ? "登录" : "注册"}
            </button>
          ))}
        </div>

        <div className="space-y-3">
          <Input value={username} onChange={setUsername} placeholder="用户名（至少 3 位）" />
          <Input
            value={password}
            onChange={setPassword}
            placeholder="密码（至少 8 位）"
            type="password"
            onKeyDown={(e) => e.key === "Enter" && submit()}
          />
          {mode === "register" && requiresCode && (
            <Input
              value={inviteCode}
              onChange={setInviteCode}
              placeholder="邀请码（向管理员获取）"
              onKeyDown={(e) => e.key === "Enter" && submit()}
            />
          )}
          {error && <div className="rounded-lg bg-rose-500/10 px-3 py-2 text-xs text-rose-300">{error}</div>}
          <Button onClick={submit} loading={loading} className="w-full">
            {mode === "login" ? "登录" : "创建账号"}
          </Button>
        </div>

        <p className="mt-4 text-center text-[11px] leading-5 text-zinc-600">
          首次使用请先注册。未配置模型 API Key 时以 Mock 模式运行，全部功能可离线体验。
        </p>
      </div>
    </div>
  );
}
