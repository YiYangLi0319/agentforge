import { Coins, Cpu, RefreshCw, Shield, ThumbsUp, Users } from "lucide-react";
import { useCallback, useEffect, useState } from "react";

import { Badge, Button, formatCost } from "../components/ui";
import { api } from "../lib/api";
import type { AdminUser } from "../lib/types";

interface GlobalStats {
  totals: { users: number; runs: number; tokens: number; cost: number; knowledge_bases: number };
  feedback: { up: number; down: number; satisfaction: number };
  trend: { day: string; runs: number; tokens: number; cost: number }[];
}

export default function AdminPage() {
  const [users, setUsers] = useState<AdminUser[]>([]);
  const [stats, setStats] = useState<GlobalStats | null>(null);
  const [loading, setLoading] = useState(false);
  const [denied, setDenied] = useState(false);

  const load = useCallback(async () => {
    setLoading(true);
    try {
      const [u, s] = await Promise.all([
        api.get<AdminUser[]>("/api/admin/users"),
        api.get<GlobalStats>("/api/admin/stats"),
      ]);
      setUsers(u);
      setStats(s);
    } catch (e) {
      if (e && typeof e === "object" && "status" in e && (e as { status: number }).status === 403) setDenied(true);
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => {
    load();
  }, [load]);

  const setQuota = async (u: AdminUser) => {
    const input = prompt(`设置 ${u.username} 的每日 token 额度（0=不限）`, String(u.quota));
    if (input === null) return;
    const val = Number(input);
    if (Number.isNaN(val) || val < 0) return;
    await api.patch(`/api/admin/users/${u.id}/quota`, { daily_token_quota: val });
    load();
  };

  if (denied) {
    return (
      <div className="flex h-full flex-col items-center justify-center gap-2 text-zinc-500">
        <Shield size={26} />
        <div className="text-sm">需要管理员权限</div>
        <div className="text-xs">在部署环境变量里设置 ADMIN_USERNAME 为你的用户名即可获得管理权限</div>
      </div>
    );
  }

  return (
    <div className="h-full overflow-y-auto">
      <div className="mx-auto max-w-5xl space-y-5 px-6 py-6">
        <div className="flex items-center justify-between">
          <h1 className="flex items-center gap-2 text-lg font-semibold text-zinc-100">
            <Shield size={19} className="text-indigo-400" /> 管理后台
          </h1>
          <Button size="sm" variant="ghost" onClick={load} loading={loading}>
            <RefreshCw size={13} /> 刷新
          </Button>
        </div>

        {stats && (
          <div className="grid grid-cols-2 gap-3 md:grid-cols-5">
            <StatCard icon={<Users size={13} />} label="用户数" value={String(stats.totals.users)} />
            <StatCard icon={<Cpu size={13} />} label="总 Token" value={stats.totals.tokens.toLocaleString()} />
            <StatCard icon={<Coins size={13} />} label="总成本" value={formatCost(stats.totals.cost)} />
            <StatCard icon={<RefreshCw size={13} />} label="运行数" value={String(stats.totals.runs)} />
            <StatCard
              icon={<ThumbsUp size={13} />}
              label="满意度"
              value={`${(stats.feedback.satisfaction * 100).toFixed(0)}%`}
              sub={`赞 ${stats.feedback.up} / 踩 ${stats.feedback.down}`}
            />
          </div>
        )}

        <div className="overflow-hidden rounded-xl border border-zinc-800">
          <table className="w-full text-[13px]">
            <thead>
              <tr className="border-b border-zinc-800 bg-zinc-900/70 text-left text-[11px] text-zinc-500">
                <th className="px-4 py-2.5 font-medium">用户</th>
                <th className="px-3 py-2.5 font-medium">今日用量</th>
                <th className="px-3 py-2.5 font-medium">每日额度</th>
                <th className="px-3 py-2.5 font-medium">总 Token</th>
                <th className="px-3 py-2.5 font-medium">总成本</th>
                <th className="px-3 py-2.5 font-medium">运行数</th>
                <th className="px-3 py-2.5" />
              </tr>
            </thead>
            <tbody>
              {users.map((u) => (
                <tr key={u.id} className="border-b border-zinc-800/60 last:border-0 hover:bg-zinc-900/40">
                  <td className="px-4 py-2.5">
                    <span className="text-zinc-200">{u.username}</span>
                    {u.is_admin && (
                      <Badge tone="indigo">
                        <Shield size={9} /> admin
                      </Badge>
                    )}
                  </td>
                  <td className="px-3 py-2.5 font-mono text-zinc-400">{u.used_today.toLocaleString()}</td>
                  <td className="px-3 py-2.5 font-mono text-zinc-400">{u.quota === 0 ? "不限" : u.quota.toLocaleString()}</td>
                  <td className="px-3 py-2.5 font-mono text-zinc-400">{u.total_tokens.toLocaleString()}</td>
                  <td className="px-3 py-2.5 font-mono text-zinc-500">{formatCost(u.total_cost)}</td>
                  <td className="px-3 py-2.5 font-mono text-zinc-500">{u.run_count}</td>
                  <td className="px-3 py-2.5 text-right">
                    <button onClick={() => setQuota(u)} className="text-[11px] text-indigo-400 hover:text-indigo-300">
                      改额度
                    </button>
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      </div>
    </div>
  );
}

function StatCard({ icon, label, value, sub }: { icon: React.ReactNode; label: string; value: string; sub?: string }) {
  return (
    <div className="rounded-xl border border-zinc-800 bg-zinc-900/60 p-3.5">
      <div className="mb-1.5 flex items-center gap-1.5 text-[11px] text-zinc-500">
        {icon}
        {label}
      </div>
      <div className="text-xl font-semibold text-zinc-100">{value}</div>
      {sub && <div className="mt-0.5 text-[10px] text-zinc-500">{sub}</div>}
    </div>
  );
}
