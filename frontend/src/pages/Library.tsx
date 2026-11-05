import { useState } from "react";
import { useQuery, useQueryClient } from "@tanstack/react-query";
import { Link, useNavigate } from "react-router-dom";
import {
  Search,
  Plus,
  FolderOpen,
  ArrowUpRight,
  Play,
  Download,
  Trash2,
  Clock,
  Clapperboard,
  ChevronLeft,
  ChevronRight,
  RefreshCw,
  SlidersHorizontal,
} from "lucide-react";
import { api, post, active, date, duration, statusNames } from "../api";
import {
  Button,
  Modal,
  ErrorBox,
  Empty,
  Loading,
  Toast,
} from "../components/ui";
import type { Work, Job } from "../types";
export default function Library() {
  const navigate = useNavigate();
  const client = useQueryClient();
  const [search, setSearch] = useState("");
  const [status, setStatus] = useState("");
  const [sort, setSort] = useState("created_at");
  const [page, setPage] = useState(1);
  const [deleting, setDeleting] = useState<Work | null>(null);
  const [error, setError] = useState<unknown>();
  const [toast, setToast] = useState("");
  const works = useQuery({
    queryKey: ["works", search, status, sort, page],
    queryFn: () =>
      api<{ tasks: Work[]; total: number; total_pages: number }>(
        `/works?search=${encodeURIComponent(search)}&status=${status}&sort_by=${sort}&page=${page}`,
      ),
  });
  const jobs = useQuery({
    queryKey: ["jobs"],
    queryFn: () => api<Job[]>("/jobs"),
    refetchInterval: 2000,
  });
  const running = jobs.data?.filter((j) => active(j.status)) || [];
  return (
    <div className="library-page">
      <div className="page-heading">
        <div>
          <div className="eyebrow">YOUR CREATIVE COLLECTION</div>
          <h1>
            每一个作品，都有你的印记<span className="heading-dot">。</span>
          </h1>
          <p>找回灵感，继续打磨。好故事值得被收藏。</p>
        </div>
        <Button onClick={() => navigate("/")}>
          <Plus size={18} />
          开始新创作
        </Button>
      </div>
      {running.length > 0 && (
        <section className="running-strip">
          <div className="running-icon">
            <Clapperboard size={21} />
          </div>
          <div>
            <strong>{running.length} 个任务正在进行</strong>
            <p>你可以继续创作，我们会替你保存进度。</p>
          </div>
          <div className="running-jobs">
            {running.slice(0, 2).map((j) => (
              <Link key={j.id} to={"/works/" + j.work_id}>
                <span>{j.params.text?.slice(0, 20) || "作品编辑"}</span>
                <progress max={1} value={j.progress} />
              </Link>
            ))}
          </div>
        </section>
      )}
      <div className="library-toolbar">
        <div className="search-box">
          <Search size={18} />
          <input
            aria-label="搜索作品"
            placeholder="搜索标题或创作内容"
            value={search}
            onChange={(e) => {
              setSearch(e.target.value);
              setPage(1);
            }}
          />
        </div>
        <div className="toolbar-filters">
          <select
            aria-label="按状态筛选"
            value={status}
            onChange={(e) => {
              setStatus(e.target.value);
              setPage(1);
            }}
          >
            <option value="">全部状态</option>
            {Object.entries(statusNames).map(([key, label]) => (
              <option key={key} value={key}>
                {label}
              </option>
            ))}
          </select>
          <select
            aria-label="作品排序"
            value={sort}
            onChange={(e) => setSort(e.target.value)}
          >
            <option value="created_at">最近创作</option>
            <option value="updated_at">最近修改</option>
            <option value="title">标题排序</option>
          </select>
        </div>
      </div>
      <div className="collection-label">
        <strong>我的作品</strong>
        <span>{works.data?.total || 0} 个作品</span>
        <span className="collection-type">视频创作</span>
      </div>
      <ErrorBox error={works.error || error} />
      {works.isPending ? (
        <Loading />
      ) : !works.data?.tasks.length ? (
        <Empty
          icon={<FolderOpen size={32} />}
          title={
            search || status
              ? "没有找到匹配的作品"
              : "你的第一个作品，从这里开始"
          }
        >
          {search || status
            ? "换个关键词或筛选条件试试。"
            : "在创作工作台写下一个想法，让它成为你的第一段故事。"}
          <br />
          <Button variant="secondary" onClick={() => navigate("/")}>
            <Plus size={16} />
            去创作
          </Button>
        </Empty>
      ) : (
        <div className="works-grid">
          {works.data.tasks.map((work) => (
            <article className="work-card" key={work.id}>
              <Link
                to={"/works/" + work.id}
                className="work-cover"
                aria-label={"打开作品 " + work.title}
              >
                {work.cover ? (
                  <img
                    src={`/api/works/${work.id}/media?field=cover`}
                    alt={work.title}
                    loading="lazy"
                  />
                ) : (
                  <div className="work-placeholder">
                    <Clapperboard size={30} />
                    <span>
                      {active(work.status)
                        ? "故事正在发生"
                        : "等待故事的下一幕"}
                    </span>
                  </div>
                )}
                <span className={`status-badge ${work.status}`}>
                  <i />
                  {statusNames[work.status] || work.status}
                </span>
                {work.video_path && (
                  <span className="cover-play">
                    <Play size={18} fill="currentColor" />
                  </span>
                )}
                {work.duration > 0 && (
                  <span className="cover-duration">
                    {duration(work.duration)}
                  </span>
                )}
              </Link>
              <div className="work-card-body">
                <Link className="work-title" to={"/works/" + work.id}>
                  {work.title}
                </Link>
                <div className="work-meta">
                  <span>{date(work.created_at)}</span>
                  <span>{work.n_frames} 个分镜</span>
                </div>
                {work.error && <p className="work-error">{work.error}</p>}
                <div className="work-card-actions">
                  <Link to={"/works/" + work.id}>
                    查看与编辑
                    <ArrowUpRight size={14} />
                  </Link>
                  <div>
                    <a
                      className={`icon-button ${!work.video_path ? "disabled" : ""}`}
                      href={
                        work.video_path
                          ? `/api/works/${work.id}/media?download=true`
                          : undefined
                      }
                      title="下载视频"
                      aria-label={"下载 " + work.title}
                    >
                      <Download size={17} />
                    </a>
                    <button
                      className="icon-button"
                      aria-label={"删除 " + work.title}
                      onClick={() => setDeleting(work)}
                    >
                      <Trash2 size={16} />
                    </button>
                  </div>
                </div>
              </div>
            </article>
          ))}
        </div>
      )}
      {works.data && works.data.total_pages > 1 && (
        <div className="pagination">
          <Button
            variant="secondary"
            disabled={page <= 1}
            onClick={() => setPage((p) => p - 1)}
          >
            <ChevronLeft size={17} />
            上一页
          </Button>
          <span>
            {page} / {works.data.total_pages}
          </span>
          <Button
            variant="secondary"
            disabled={page >= works.data.total_pages}
            onClick={() => setPage((p) => p + 1)}
          >
            下一页
            <ChevronRight size={17} />
          </Button>
        </div>
      )}
      {deleting && (
        <Modal title="删除这个作品？" onClose={() => setDeleting(null)}>
          <p>「{deleting.title}」的成片、分镜素材和修订记录将一并删除。</p>
          <div className="modal-actions">
            <Button variant="secondary" onClick={() => setDeleting(null)}>
              保留作品
            </Button>
            <Button
              variant="danger"
              onClick={async () => {
                try {
                  await api("/works/" + deleting.id, { method: "DELETE" });
                  setDeleting(null);
                  setToast("删除任务已提交");
                  client.invalidateQueries({ queryKey: ["jobs"] });
                } catch (e) {
                  setError(e);
                  setDeleting(null);
                }
              }}
            >
              确认删除
            </Button>
          </div>
        </Modal>
      )}
      {toast && <Toast message={toast} onClose={() => setToast("")} />}
    </div>
  );
}
