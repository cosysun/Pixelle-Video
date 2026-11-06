import { useEffect, useState } from "react";
import { Navigate, NavLink, Route, Routes, useLocation, useNavigate } from "react-router-dom";
import { useQuery, useQueryClient } from "@tanstack/react-query";
import {
  ChartNoAxesCombined,
  ArrowRight,
  Clapperboard,
  FolderOpen,
  Settings,
  LogOut,
  Sparkles,
  ArrowUpRight,
  LockKeyhole,
  Check,
  PanelLeftClose,
} from "lucide-react";
import { api, post, active } from "./api";
import { Button, Field, ErrorBox, Loading } from "./components/ui";
import Studio from "./pages/Studio";
import Library from "./pages/Library";
import WorkDetail from "./pages/WorkDetail";
import SettingsPanel from "./pages/Settings";
import Investment from "./pages/investment/Investment";
import type { Job } from "./types";
export default function App() {
  const client = useQueryClient();
  const navigate = useNavigate();
  const location = useLocation();
  const breadcrumb = location.pathname.startsWith("/investment")
    ? "投资总览"
    : location.pathname.startsWith("/works/")
      ? "作品详情"
      : location.pathname.startsWith("/works")
        ? "作品库"
        : "创作工作台";
  const me = useQuery({
    queryKey: ["me"],
    queryFn: () => api<{ username: string }>("/auth/me"),
    retry: false,
  });
  const [settings, setSettings] = useState(false);
  useEffect(() => {
    const expired = () => client.setQueryData(["me"], null);
    window.addEventListener("session-expired", expired);
    return () => window.removeEventListener("session-expired", expired);
  }, [client]);
  const jobs = useQuery({
    queryKey: ["jobs"],
    queryFn: () => api<Job[]>("/jobs"),
    enabled: !!me.data,
    refetchInterval: (q) =>
      q.state.data?.some((j) => active(j.status)) ? 2000 : 10000,
  });
  const count = jobs.data?.filter((j) => active(j.status)).length || 0;
  useEffect(() => {
    if (jobs.data) client.invalidateQueries({ queryKey: ["works"] });
  }, [jobs.data]);
  if (me.isPending) return <Loading />;
  if (!me.data)
    return (
      <Login
        onSuccess={() => {
          client.invalidateQueries({ queryKey: ["me"] });
          navigate("/investment", { replace: true });
        }}
      />
    );
  return (
    <div className="app-shell">
      <aside className="sidebar">
        <NavLink className="brand" to="/investment" aria-label="Pixelle 首页">
          <span className="brand-mark">
            p<span />
          </span>
          <span>
            pixelle<small>CREATIVE STUDIO</small>
          </span>
        </NavLink>
        <div className="nav-label">你的创作空间</div>
        <nav className="main-nav">
          <NavLink to="/studio" end>
            <Sparkles size={20} />
            <span>快速创作</span>
          </NavLink>
          <NavLink to="/works">
            <FolderOpen size={20} />
            <span>作品库</span>
            {count > 0 && <span className="nav-count">{count}</span>}
          </NavLink>
          <NavLink to="/investment">
            <ChartNoAxesCombined size={20} />
            <span>投资总览</span>
          </NavLink>
        </nav>
        <div className="sidebar-note">
          <p>
            草稿自动保存，随时继续创作。
          </p>
        </div>
        <div className="sidebar-bottom">
          <button onClick={() => setSettings(true)}>
            <Settings size={19} />
            <span>创作设置</span>
          </button>
          <div className="profile">
            <div className="avatar">{me.data.username[0].toUpperCase()}</div>
            <div>
              <strong>{me.data.username}</strong>
              <small>个人工作空间</small>
            </div>
            <button
              className="icon-button"
              title="退出登录"
              aria-label="退出登录"
              onClick={async () => {
                await post("/auth/logout");
                client.clear();
                navigate("/");
              }}
            >
              <LogOut size={17} />
            </button>
          </div>
        </div>
      </aside>
      <div className="main-shell">
        <header className="topbar">
          <span className="breadcrumb">
            工作空间 <span>/</span> <strong>{breadcrumb}</strong>
          </span>
          <div className="topbar-right">
            {count > 0 ? (
              <span className="live-status">
                <i />
                {count} 个任务进行中
              </span>
            ) : (
              <span className="ready-status">
                <i />
                准备好创作了
              </span>
            )}
            <button
              className="mobile-settings icon-button"
              aria-label="创作设置"
              onClick={() => setSettings(true)}
            >
              <Settings size={20} />
            </button>
          </div>
        </header>
        <main>
          <Routes>
            <Route path="/" element={<Navigate to="/investment" replace />} />
            <Route
              path="/studio"
              element={<Studio onSettings={() => setSettings(true)} />}
            />
            <Route path="/investment" element={<Investment />} />
            <Route path="/works" element={<Library />} />
            <Route path="/works/:id" element={<WorkDetail />} />
            <Route
              path="*"
              element={<Navigate to="/investment" replace />}
            />
          </Routes>
        </main>
        <footer className="app-footer">
          给灵感一点空间。<span>MADE WITH PIXELLE</span>
        </footer>
      </div>
      <nav className="mobile-nav">
        <NavLink to="/studio" end>
          <Sparkles size={21} />
          创作
        </NavLink>
        <NavLink to="/works">
          <FolderOpen size={21} />
          作品{count > 0 && <i />}
        </NavLink>
        <NavLink to="/investment">
          <ChartNoAxesCombined size={21} />
          投资
        </NavLink>
      </nav>
      {settings && <SettingsPanel onClose={() => setSettings(false)} />}
    </div>
  );
}
function Login({ onSuccess }: { onSuccess: () => void }) {
  const [username, setUsername] = useState("");
  const [password, setPassword] = useState("");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<unknown>();
  return (
    <div className="login-page">
      <div className="login-story">
        <div className="brand">
          <span className="brand-mark">
            p<span />
          </span>
          <span>pixelle</span>
        </div>
        <div className="login-story-main">
          <span className="eyebrow">A SPACE FOR YOUR IDEAS</span>
          <h1>
            每一个想法，
            <br />
            都值得<span>被看见。</span>
          </h1>
          <p>
            把脑海中的灵感，变成有温度的作品。
            <br />
            你的创作空间，就在这里。
          </p>
          <div className="login-art">
            <div className="art-card one">
              <Sparkles size={26} />
              <span>一点灵感</span>
            </div>
            <div className="art-card two">
              <Clapperboard size={30} />
              <span>一段故事</span>
            </div>
            <div className="art-card three">
              <Check size={25} />
              <span>一个作品</span>
            </div>
          </div>
        </div>
        <small>YOUR IDEAS, BEAUTIFULLY TOLD.</small>
      </div>
      <div className="login-form-side">
        <form
          className="login-form"
          onSubmit={async (e) => {
            e.preventDefault();
            setError(undefined);
            setBusy(true);
            try {
              await post("/auth/login", { username, password });
              onSuccess();
            } catch (e) {
              setError(e);
            } finally {
              setBusy(false);
            }
          }}
        >
          <div className="login-lock">
            <LockKeyhole size={24} />
          </div>
          <h2>欢迎回到创作空间</h2>
          <p>登录，继续你的下一个好故事。</p>
          <Field label="账号">
            <input
              autoComplete="username"
              required
              value={username}
              onChange={(e) => setUsername(e.target.value)}
              placeholder="输入你的账号"
            />
          </Field>
          <Field label="密码">
            <input
              type="password"
              autoComplete="current-password"
              required
              value={password}
              onChange={(e) => setPassword(e.target.value)}
              placeholder="输入你的密码"
            />
          </Field>
          <ErrorBox error={error} />
          <Button busy={busy} type="submit">
            进入工作台 <ArrowRight size={18} />
          </Button>
          <small className="login-private">
            <LockKeyhole size={13} />
            私人的空间，自由的创作。
          </small>
        </form>
      </div>
    </div>
  );
}
