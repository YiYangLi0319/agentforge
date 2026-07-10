import {
  Activity,
  BarChart3,
  BookOpenText,
  Boxes,
  Gauge,
  LogOut,
  Menu,
  MessagesSquare,
  Shield,
  Sparkles,
  Telescope,
  Wrench,
  X,
} from "lucide-react";
import { useEffect, useState } from "react";
import { NavLink, Outlet } from "react-router-dom";

import { api } from "../lib/api";
import type { MeInfo } from "../lib/types";
import { useAuth } from "../stores/auth";

interface MetaInfo {
  llm: { provider: string; model: string };
  mock_mode: boolean;
}

const NAV = [
  { to: "/chat", label: "智能对话", icon: MessagesSquare },
  { to: "/research", label: "深度研究", icon: Telescope },
  { to: "/agents", label: "自定义 Agent", icon: Sparkles },
  { to: "/knowledge", label: "知识库", icon: BookOpenText },
  { to: "/data", label: "数据分析", icon: BarChart3 },
  { to: "/tools", label: "工具生态", icon: Wrench },
  { to: "/traces", label: "运行追踪", icon: Activity },
  { to: "/dashboard", label: "可观测看板", icon: Gauge },
];

export default function Layout() {
  const { username, logout } = useAuth();
  const [meta, setMeta] = useState<MetaInfo | null>(null);
  const [me, setMe] = useState<MeInfo | null>(null);
  const [mobileNavOpen, setMobileNavOpen] = useState(false);

  useEffect(() => {
    api.get<MetaInfo>("/api/meta").then(setMeta).catch(() => undefined);
    api.get<MeInfo>("/api/auth/me").then(setMe).catch(() => undefined);
  }, []);

  return (
    <div className="flex h-full flex-col">
      <header className="flex h-14 shrink-0 items-center justify-between border-b border-zinc-800/80 bg-zinc-950 px-4 md:hidden">
        <div className="flex items-center gap-2.5">
          <div className="flex h-8 w-8 items-center justify-center rounded-lg bg-gradient-to-br from-indigo-500 to-violet-600">
            <Boxes size={17} className="text-white" />
          </div>
          <span className="text-sm font-bold tracking-wide text-zinc-100">AgentForge</span>
        </div>
        <button
          type="button"
          onClick={() => setMobileNavOpen(true)}
          aria-label="打开导航"
          className="rounded-lg p-2 text-zinc-400 hover:bg-zinc-800 hover:text-zinc-100"
        >
          <Menu size={19} />
        </button>
      </header>

      <div className="flex min-h-0 flex-1">
        {mobileNavOpen && (
          <button
            type="button"
            aria-label="关闭导航"
            className="fixed inset-0 z-40 bg-black/60 md:hidden"
            onClick={() => setMobileNavOpen(false)}
          />
        )}
        <aside
          className={
            "fixed inset-y-0 left-0 z-50 flex w-64 shrink-0 flex-col border-r border-zinc-800/80 bg-zinc-950 transition-transform duration-200 md:static md:z-auto md:w-56 md:translate-x-0 md:bg-zinc-900/40 " +
            (mobileNavOpen ? "translate-x-0" : "-translate-x-full")
          }
        >
        <div className="flex items-center gap-2.5 px-5 py-5">
          <div className="flex h-9 w-9 items-center justify-center rounded-xl bg-gradient-to-br from-indigo-500 to-violet-600 shadow-lg shadow-indigo-950">
            <Boxes size={19} className="text-white" />
          </div>
          <div>
            <div className="text-[15px] font-bold tracking-wide text-zinc-100">AgentForge</div>
            <div className="text-[10px] text-zinc-500">企业级多智能体平台</div>
          </div>
          <button
            type="button"
            onClick={() => setMobileNavOpen(false)}
            aria-label="关闭导航"
            className="ml-auto rounded-lg p-1.5 text-zinc-500 hover:bg-zinc-800 md:hidden"
          >
            <X size={17} />
          </button>
        </div>

        <nav className="mt-2 flex-1 space-y-1 overflow-y-auto px-3">
          {NAV.map(({ to, label, icon: Icon }) => (
            <NavLink
              key={to}
              to={to}
              onClick={() => setMobileNavOpen(false)}
              className={({ isActive }) =>
                "flex items-center gap-2.5 rounded-lg px-3 py-2.5 text-[13px] transition-colors " +
                (isActive
                  ? "bg-indigo-500/15 font-medium text-indigo-300"
                  : "text-zinc-400 hover:bg-zinc-800/60 hover:text-zinc-200")
              }
            >
              <Icon size={16} />
              {label}
            </NavLink>
          ))}
          {me?.is_admin && (
            <NavLink
              to="/admin"
              onClick={() => setMobileNavOpen(false)}
              className={({ isActive }) =>
                "flex items-center gap-2.5 rounded-lg px-3 py-2.5 text-[13px] transition-colors " +
                (isActive
                  ? "bg-indigo-500/15 font-medium text-indigo-300"
                  : "text-zinc-400 hover:bg-zinc-800/60 hover:text-zinc-200")
              }
            >
              <Shield size={16} />
              管理后台
            </NavLink>
          )}
        </nav>

        <div className="border-t border-zinc-800/80 p-3">
          {me && !me.quota.unlimited && me.quota.limit > 0 && (
            <div className="mb-2 rounded-lg bg-zinc-900 px-3 py-2">
              <div className="mb-1 flex items-center justify-between text-[10px] text-zinc-500">
                <span>今日额度</span>
                <span className="font-mono">
                  {me.quota.used.toLocaleString()}/{me.quota.limit.toLocaleString()}
                </span>
              </div>
              <div className="h-1.5 overflow-hidden rounded-full bg-zinc-800">
                <div
                  className="h-full rounded-full bg-indigo-500"
                  style={{ width: `${Math.min((me.quota.used / me.quota.limit) * 100, 100)}%` }}
                />
              </div>
            </div>
          )}
          {meta && (
            <div className="mb-2 rounded-lg bg-zinc-900 px-3 py-2">
              <div className="text-[10px] text-zinc-500">当前模型</div>
              <div className="truncate font-mono text-[11px] text-zinc-300">
                {meta.llm.provider}/{meta.llm.model}
              </div>
              {meta.mock_mode && (
                <div className="mt-1 rounded bg-amber-500/15 px-1.5 py-0.5 text-[10px] text-amber-300">
                  Mock 演示模式 · 配置 API Key 后接入真实模型
                </div>
              )}
            </div>
          )}
          <div className="flex items-center justify-between px-1">
            <span className="text-xs text-zinc-400">{username}</span>
            <button
              onClick={logout}
              title="退出登录"
              className="rounded-md p-1.5 text-zinc-500 hover:bg-zinc-800 hover:text-zinc-200"
            >
              <LogOut size={14} />
            </button>
          </div>
        </div>
        </aside>

        <main className="min-w-0 flex-1">
          <Outlet />
        </main>
      </div>
    </div>
  );
}
