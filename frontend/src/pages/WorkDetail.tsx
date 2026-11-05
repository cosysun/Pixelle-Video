import { useEffect, useState } from "react";
import { useParams, useNavigate, Link } from "react-router-dom";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import {
  ArrowLeft,
  Download,
  Copy,
  RefreshCw,
  Plus,
  Trash2,
  Mic,
  Image,
  Music2,
  Type,
  Layers,
  Play,
  Check,
  ChevronRight,
  Clapperboard,
  Volume2,
  ArrowUpRight,
  Settings2,
} from "lucide-react";
import { api, post, active, duration, statusNames } from "../api";
import {
  Button,
  Field,
  Modal,
  ErrorBox,
  Loading,
  Toast,
  Accordion,
  Empty,
} from "../components/ui";
import type { Detail, Job, Asset, Models, Frame } from "../types";
import { initialDraft, templateName } from "./Studio";
export default function WorkDetail() {
  const { id = "" } = useParams();
  const navigate = useNavigate();
  const client = useQueryClient();
  const [selected, setSelected] = useState(0);
  const [editing, setEditing] = useState(false);
  const [dialog, setDialog] = useState("");
  const [title, setTitle] = useState("");
  const [narration, setNarration] = useState("");
  const [prompt, setPrompt] = useState("");
  const [bgm, setBgm] = useState("");
  const [volume, setVolume] = useState(0.2);
  const [template, setTemplate] = useState("");
  const [ttsMode, setTtsMode] = useState("local");
  const [voice, setVoice] = useState("zh-CN-XiaoxiaoNeural");
  const [ttsModel, setTtsModel] = useState("speech-2.8-turbo");
  const [speed, setSpeed] = useState(1);
  const [media, setMedia] = useState("");
  const [firstFrame, setFirstFrame] = useState("");
  const [position, setPosition] = useState(0);
  const [source, setSource] = useState("");
  const [persist, setPersist] = useState(false);
  const [toast, setToast] = useState("");
  const [error, setError] = useState<unknown>();
  const detail = useQuery({
    queryKey: ["work", id],
    queryFn: () => api<Detail>("/works/" + id),
    refetchInterval: 10000,
  });
  const jobs = useQuery({
    queryKey: ["jobs"],
    queryFn: () => api<Job[]>("/jobs"),
    refetchInterval: 2000,
  });
  const current = jobs.data?.find((j) => j.work_id === id && active(j.status));
  const resources = useQuery({
    queryKey: ["resources"],
    queryFn: () => api<Asset[]>("/resources"),
  });
  const models = useQuery({
    queryKey: ["models"],
    queryFn: () => api<Models>("/models"),
  });
  const voices = useQuery({
    queryKey: ["voices", ttsMode],
    queryFn: () =>
      api<{ id: string; name: string }[]>("/voices?mode=" + ttsMode),
    enabled: ttsMode === "local" || models.data?.tts_api_configured,
  });
  const storyboard = detail.data?.storyboard;
  const frames = storyboard?.frames || [];
  const frame = frames[selected];
  useEffect(() => {
    if (storyboard) setTitle(storyboard.title);
  }, [storyboard?.title]);
  useEffect(() => {
    if (frame) {
      setNarration(frame.narration);
      setPrompt(frame.image_prompt || "");
      setMedia(
        storyboard?.config.media_workflow?.startsWith("api/")
          ? storyboard.config.media_workflow
          : "",
      );
    }
  }, [selected, frame?.narration, frame?.image_prompt]);
  const edit = useMutation({
    mutationFn: (body: { action: string; values?: Record<string, any> }) =>
      post("/works/" + id + "/edit", body),
    onSuccess: () => {
      setDialog("");
      setEditing(false);
      setToast("修改已提交，完成后会更新作品");
      client.invalidateQueries({ queryKey: ["jobs"] });
    },
  });
  useEffect(() => {
    if (jobs.data && !current)
      client.invalidateQueries({ queryKey: ["work", id] });
  }, [jobs.data]);
  const audioOverrides = () => ({
    tts_inference_mode: ttsMode,
    voice_id: ttsMode === "local" ? voice : null,
    tts_voice_id: ttsMode === "api" ? voice : null,
    tts_provider: "minimax",
    tts_model: ttsModel,
    tts_speed: speed,
  });
  const run = (action: string, values: Record<string, any> = {}) => {
    setError(undefined);
    edit.mutate({ action, values });
  };
  const open = (name: string) => {
    setEditing(false);
    setDialog(name);
    setPersist(false);
    if (frame && name !== "insert") {
      setNarration(frame.narration);
      setPrompt(frame.image_prompt || "");
    }
    if (name === "insert") {
      setNarration("");
      setPrompt("");
      setPosition(selected + 1);
      setSource("");
    }
    if (name === "bgm") {
      setBgm(detail.data?.metadata.input.bgm_id || "");
      setVolume(detail.data?.metadata.input.bgm_volume || 0.2);
    }
    if (name === "audio" || name === "all-audio") {
      const cfg = storyboard?.config || {};
      setTtsMode(
        ["api", "local"].includes(cfg.tts_inference_mode)
          ? cfg.tts_inference_mode
          : "local",
      );
      setVoice(cfg.tts_voice_id || cfg.voice_id || "zh-CN-XiaoxiaoNeural");
      setSpeed(cfg.tts_speed || 1);
      setTtsModel(cfg.tts_model || "speech-2.8-turbo");
    }
  };
  if (detail.isPending) return <Loading />;
  if (!detail.data)
    return (
      <div className="detail-page">
        <Link className="back-link" to="/works">
          <ArrowLeft size={16} />
          回到作品库
        </Link>
        <ErrorBox error={detail.error} />
      </div>
    );
  const input = detail.data.metadata.input;
  const isCompleted = detail.data.metadata.status === "completed";
  const titleText = storyboard?.title || input.title || "未完成的故事";
  return (
    <div className="detail-page">
      <Link className="back-link" to="/works">
        <ArrowLeft size={16} />
        回到作品库
      </Link>
      <div className="detail-heading">
        <div>
          <span className={`status-badge ${detail.data.metadata.status}`}>
            <i />
            {statusNames[detail.data.metadata.status]}
          </span>
          <h1>{titleText}</h1>
          <p>
            {frames.length} 个分镜 ·{" "}
            {duration(frames.reduce((s, f) => s + f.duration, 0))} ·
            每一处细节，都可以继续打磨。
          </p>
        </div>
        <div className="detail-top-actions">
          <Button
            variant="secondary"
            onClick={() => {
              const compatible = Object.fromEntries(
                Object.entries(input).filter(([k]) => k in initialDraft),
              );
              const templateAsset = resources.data?.find(
                (a) => a.metadata.template_path === input.frame_template,
              );
              localStorage.setItem(
                "pixelle-draft",
                JSON.stringify({
                  ...initialDraft,
                  ...compatible,
                  template_id: templateAsset?.id || input.template_id || "",
                }),
              );
              navigate("/");
            }}
          >
            <Copy size={16} />
            再次创作
          </Button>
          {detail.data.metadata.result?.video_path && (
            <a
              className="button primary"
              href={`/api/works/${id}/media?download=true`}
            >
              <Download size={16} />
              下载作品
            </a>
          )}
        </div>
      </div>
      {current && (
        <div className="detail-progress">
          <div>
            <span>{current.message || "正在处理作品"}</span>
            <strong>{Math.round(current.progress * 100)}%</strong>
            <button onClick={() => post("/jobs/" + current.id + "/cancel")}>
              取消任务
            </button>
          </div>
          <progress max={1} value={current.progress} />
        </div>
      )}
      <ErrorBox error={detail.data.metadata.error || error || edit.error} />
      {!isCompleted && !current && (
        <div className="recovery-banner">
          <div>
            <strong>继续完成这个故事</strong>
            <p>已完成的分镜素材会被复用。旧工作流需要先选择新的模型。</p>
          </div>
          <Button onClick={() => open("resume")}>
            <RefreshCw size={16} />
            恢复创作
          </Button>
        </div>
      )}
      <div className="detail-workspace">
        <section className="scene-list">
          <div className="scene-list-title">
            <h2>故事分镜</h2>
            <button
              className="icon-button"
              title="添加分镜"
              aria-label="添加分镜"
              disabled={!!current}
              onClick={() => open("insert")}
            >
              <Plus size={18} />
            </button>
          </div>
          {frames.map((f, i) => (
            <button
              className={`scene-item ${selected === i ? "selected" : ""}`}
              key={i}
              onClick={() => setSelected(i)}
            >
              <span className="scene-number">
                {String(i + 1).padStart(2, "0")}
              </span>
              <div className="scene-thumbnail">
                {f.composed_image_path || f.image_path ? (
                  <img
                    src={`/api/works/${id}/media?frame=${i}&field=${f.composed_image_path ? "composed_image_path" : "image_path"}`}
                    alt=""
                  />
                ) : (
                  <Image size={20} />
                )}
              </div>
              <div>
                <strong>{f.narration}</strong>
                <span>
                  {duration(f.duration)}
                  {f.image_source_index != null ? " · 共享画面" : ""}
                </span>
              </div>
              <ChevronRight size={14} />
            </button>
          ))}
        </section>
        <section className="detail-preview">
          <div className="detail-player">
            {detail.data.metadata.result?.video_path ? (
              <video
                key={detail.data.metadata.result.video_path}
                controls
                playsInline
                src={`/api/works/${id}/media`}
              />
            ) : (
              <Empty icon={<Clapperboard size={32} />} title="故事还在路上">
                完成生成后，你的作品会出现在这里。
              </Empty>
            )}
          </div>
          <div className="detail-tools">
            <button disabled={!!current} onClick={() => open("title")}>
              <Type size={17} />
              标题
            </button>
            <button disabled={!!current} onClick={() => open("bgm")}>
              <Music2 size={17} />
              音乐
            </button>
            <button disabled={!!current} onClick={() => open("template")}>
              <Layers size={17} />
              模板
            </button>
            <button disabled={!!current} onClick={() => open("all-audio")}>
              <Mic size={17} />
              全部配音
            </button>
            <button disabled={!!current} onClick={() => run("recompose")}>
              <RefreshCw size={17} />
              重新合成
            </button>
          </div>
          {frame && (
            <div className="selected-scene">
              <div>
                <span>当前分镜</span>
                <strong>{String(selected + 1).padStart(2, "0")}</strong>
              </div>
              <p>{frame.narration}</p>
              <Button
                variant="secondary"
                disabled={!!current}
                onClick={() => setEditing(true)}
              >
                <Settings2 size={16} />
                编辑这个分镜
              </Button>
            </div>
          )}
        </section>
        <section className="scene-editor desktop-scene-editor">
          {frame ? (
            <SceneEditor
              frame={frame}
              selected={selected}
              id={id}
              disabled={!!current}
              open={open}
            />
          ) : (
            <Empty icon={<FileTextIcon />} title="还没有分镜">
              恢复创作后继续编辑。
            </Empty>
          )}
        </section>
      </div>
      {editing && frame && (
        <Modal
          title={`编辑分镜 ${String(selected + 1).padStart(2, "0")}`}
          onClose={() => setEditing(false)}
        >
          <SceneEditor
            frame={frame}
            selected={selected}
            id={id}
            disabled={!!current}
            open={open}
          />
        </Modal>
      )}
      {dialog && (
        <Modal
          title={
            {
              title: "为故事起一个名字",
              bgm: "调整背景音乐",
              template: "换一种表达风格",
              audio: "重新生成这段配音",
              media: "重新创作这个画面",
              "all-audio": "为整个故事重新配音",
              insert: "添加一个新分镜",
              delete: "删除这个分镜？",
              resume: "恢复你的创作",
            }[dialog] || "编辑作品"
          }
          onClose={() => setDialog("")}
          wide={dialog === "insert"}
        >
          {dialog === "title" ? (
            <>
              <Field label="作品标题">
                <input
                  value={title}
                  onChange={(e) => setTitle(e.target.value)}
                />
              </Field>
              <Button
                busy={edit.isPending}
                disabled={!title.trim()}
                onClick={() => run("update_title", { title })}
              >
                保存并更新作品
              </Button>
            </>
          ) : dialog === "bgm" ? (
            <>
              <Field label="背景音乐">
                <select value={bgm} onChange={(e) => setBgm(e.target.value)}>
                  <option value="">不添加音乐</option>
                  {resources.data
                    ?.filter((a) => a.kind === "bgm")
                    .map((a) => (
                      <option key={a.id} value={a.id}>
                        {a.name}
                      </option>
                    ))}
                </select>
              </Field>
              <Field label={`音乐音量 · ${Math.round(volume * 100)}%`}>
                <input
                  type="range"
                  min={0}
                  max={1}
                  step={0.05}
                  value={volume}
                  onChange={(e) => setVolume(+e.target.value)}
                />
              </Field>
              <Button
                busy={edit.isPending}
                onClick={() =>
                  run("update_bgm", { bgm_id: bgm || null, bgm_volume: volume })
                }
              >
                更新背景音乐
              </Button>
            </>
          ) : dialog === "template" ? (
            <>
              <p className="muted">选择与原作品同类型的模板，保留已有素材。</p>
              <Field label="新模板">
                <select
                  value={template}
                  onChange={(e) => setTemplate(e.target.value)}
                >
                  <option value="">选择风格</option>
                  {resources.data
                    ?.filter((a) => a.kind === "template")
                    .map((a) => (
                      <option key={a.id} value={a.id}>
                        {templateName(a)} · {a.metadata.orientation}
                      </option>
                    ))}
                </select>
              </Field>
              <Button
                busy={edit.isPending}
                disabled={!template}
                onClick={() =>
                  run("replace_template", { template_id: template })
                }
              >
                替换模板并更新
              </Button>
            </>
          ) : dialog === "audio" || dialog === "all-audio" ? (
            <>
              {dialog === "audio" && (
                <Field label="旁白文案">
                  <textarea
                    rows={4}
                    value={narration}
                    onChange={(e) => setNarration(e.target.value)}
                  />
                </Field>
              )}
              <div className="form-grid">
                <Field label="配音服务">
                  <select
                    value={ttsMode}
                    onChange={(e) => {
                      setTtsMode(e.target.value);
                      setVoice("");
                    }}
                  >
                    <option value="local">Edge TTS</option>
                    <option
                      value="api"
                      disabled={!models.data?.tts_api_configured}
                    >
                      MiniMax
                    </option>
                  </select>
                </Field>
                <Field label="配音音色">
                  <select
                    value={voice}
                    onChange={(e) => setVoice(e.target.value)}
                  >
                    <option value="">选择音色</option>
                    {voices.data?.map((v) => (
                      <option key={v.id} value={v.id}>
                        {v.name}
                      </option>
                    ))}
                  </select>
                </Field>
              </div>
              <Field label={`语速 · ${speed.toFixed(1)}×`}>
                <input
                  type="range"
                  min={0.5}
                  max={2}
                  step={0.1}
                  value={speed}
                  onChange={(e) => setSpeed(+e.target.value)}
                />
              </Field>
              {ttsMode === "api" && (
                <Field label="配音模型">
                  <input
                    value={ttsModel}
                    onChange={(e) => setTtsModel(e.target.value)}
                  />
                </Field>
              )}
              {dialog === "audio" && (
                <label className="checkbox-row">
                  <input
                    type="checkbox"
                    checked={persist}
                    onChange={(e) => setPersist(e.target.checked)}
                  />
                  后续分镜也使用这组配音设置
                </label>
              )}
              <Button
                busy={edit.isPending}
                disabled={!voice || (dialog === "audio" && !narration.trim())}
                onClick={() =>
                  dialog === "audio"
                    ? run("regenerate_frame_audio", {
                        frame_index: selected,
                        narration,
                        tts_overrides: audioOverrides(),
                        persist_overrides: persist,
                      })
                    : run("regenerate_all_audio", {
                        tts_overrides: audioOverrides(),
                      })
                }
              >
                重新生成配音
              </Button>
              <ErrorBox error={voices.error} />
            </>
          ) : dialog === "media" ? (
            <>
              <Field label="画面描述">
                <textarea
                  rows={5}
                  value={prompt}
                  onChange={(e) => setPrompt(e.target.value)}
                />
              </Field>
              <Field label="画面模型">
                <select
                  value={media}
                  onChange={(e) => setMedia(e.target.value)}
                >
                  <option value="">选择模型</option>
                  {(storyboard?.config.frame_template
                    ?.split("/")
                    .pop()
                    ?.startsWith("video_")
                    ? models.data?.videos || []
                    : models.data?.images || []
                  ).map((m) => (
                    <option key={m.key} value={m.key}>
                      {m.name}
                    </option>
                  ))}
                </select>
              </Field>
              <label className="checkbox-row">
                <input
                  type="checkbox"
                  checked={persist}
                  onChange={(e) => setPersist(e.target.checked)}
                />
                后续分镜也使用这个模型
              </label>
              <Button
                busy={edit.isPending}
                disabled={!prompt.trim() || !media}
                onClick={() =>
                  run("regenerate_frame_media", {
                    frame_index: selected,
                    image_prompt: prompt,
                    media_overrides: { media_workflow: media },
                    persist_overrides: persist,
                  })
                }
              >
                重新生成画面
              </Button>
            </>
          ) : dialog === "insert" ? (
            <>
              <Field label="插入位置">
                <select
                  value={position}
                  onChange={(e) => {
                    setPosition(+e.target.value);
                    setSource("");
                  }}
                >
                  {Array.from({ length: frames.length + 1 }, (_, i) => (
                    <option key={i} value={i}>
                      {i === 0
                        ? "故事开头"
                        : i === frames.length
                          ? "故事结尾"
                          : `分镜 ${i} 之后`}
                    </option>
                  ))}
                </select>
              </Field>
              <Field label="旁白文案">
                <textarea
                  rows={4}
                  value={narration}
                  onChange={(e) => setNarration(e.target.value)}
                />
              </Field>
              <Field label="画面来源">
                <select
                  value={source}
                  onChange={(e) => setSource(e.target.value)}
                >
                  <option value="">新生成一个画面</option>
                  {frames
                    .filter(
                      (f) => f.image_source_index == null && f.index < position,
                    )
                    .map((f) => (
                      <option key={f.index} value={f.index}>
                        共享分镜 {f.index + 1} 的画面
                      </option>
                    ))}
                </select>
              </Field>
              {source === "" && (
                <>
                  <Field label="画面描述">
                    <textarea
                      rows={3}
                      value={prompt}
                      onChange={(e) => setPrompt(e.target.value)}
                    />
                  </Field>
                  <Field label="画面模型">
                    <select
                      value={media}
                      onChange={(e) => setMedia(e.target.value)}
                    >
                      <option value="">沿用原模型</option>
                      {models.data?.images.map((m) => (
                        <option key={m.key} value={m.key}>
                          {m.name}
                        </option>
                      ))}
                    </select>
                  </Field>
                </>
              )}
              <Button
                busy={edit.isPending}
                disabled={!narration.trim()}
                onClick={() =>
                  run("insert_frame", {
                    position,
                    narration,
                    image_prompt: prompt || null,
                    image_source_index: source === "" ? null : +source,
                    media_overrides: media ? { media_workflow: media } : null,
                  })
                }
              >
                添加分镜并更新
              </Button>
            </>
          ) : dialog === "delete" ? (
            <>
              <p>
                将删除分镜 {selected + 1}{" "}
                及其关联的共享画面分镜。作品会重新合成，上一版修订仍保留。
              </p>
              <Button
                variant="danger"
                busy={edit.isPending}
                disabled={frames.length <= 1}
                onClick={() => {
                  run("delete_frame", { frame_index: selected });
                  setSelected(0);
                }}
              >
                确认删除分镜
              </Button>
            </>
          ) : dialog === "resume" ? (
            <>
              <Field label="画面模型">
                <select
                  value={media}
                  onChange={(e) => setMedia(e.target.value)}
                >
                  <option value="">选择替代模型</option>
                  {(storyboard?.config.frame_template
                    ?.split("/")
                    .pop()
                    ?.startsWith("video_")
                    ? models.data?.videos || []
                    : models.data?.images || []
                  ).map((m) => (
                    <option key={m.key} value={m.key}>
                      {m.name}
                    </option>
                  ))}
                </select>
              </Field>
              {storyboard?.config.frame_template
                ?.split("/")
                .pop()
                ?.startsWith("video_") && (
                <Field label="首帧图片模型">
                  <select
                    value={firstFrame}
                    onChange={(e) => setFirstFrame(e.target.value)}
                  >
                    <option value="">选择新的首帧模型</option>
                    {models.data?.images.map((m) => (
                      <option key={m.key} value={m.key}>
                        {m.name}
                      </option>
                    ))}
                  </select>
                </Field>
              )}
              <Field label="配音服务">
                <select
                  value={ttsMode}
                  onChange={(e) => setTtsMode(e.target.value)}
                >
                  <option value="local">Edge TTS</option>
                  <option
                    value="api"
                    disabled={!models.data?.tts_api_configured}
                  >
                    MiniMax
                  </option>
                </select>
              </Field>
              <Field label="配音音色">
                <select
                  value={voice}
                  onChange={(e) => setVoice(e.target.value)}
                >
                  {voices.data?.map((v) => (
                    <option key={v.id} value={v.id}>
                      {v.name}
                    </option>
                  ))}
                </select>
              </Field>
              <Button
                onClick={async () => {
                  try {
                    await post("/works/" + id + "/resume", {
                      media_workflow: media || undefined,
                      first_frame_workflow: firstFrame || undefined,
                      tts_inference_mode: ttsMode,
                      tts_voice: ttsMode === "local" ? voice : undefined,
                      tts_voice_id: ttsMode === "api" ? voice : undefined,
                    });
                    setDialog("");
                    client.invalidateQueries({ queryKey: ["jobs"] });
                  } catch (e) {
                    setError(e);
                  }
                }}
              >
                <RefreshCw size={17} />
                继续创作
              </Button>
            </>
          ) : null}
          <ErrorBox error={edit.error || error} />
        </Modal>
      )}
      {toast && <Toast message={toast} onClose={() => setToast("")} />}
    </div>
  );
}
function SceneEditor({
  frame,
  selected,
  id,
  disabled,
  open,
}: {
  frame: Frame;
  selected: number;
  id: string;
  disabled: boolean;
  open: (name: string) => void;
}) {
  return (
    <>
      <div className="scene-editor-heading">
        <span>分镜 {String(selected + 1).padStart(2, "0")}</span>
        <strong>打磨每一幕</strong>
      </div>
      <div className="scene-editor-section">
        <h3>
          <Mic size={16} />
          旁白文案
        </h3>
        <p>{frame.narration}</p>
        {frame.audio_path && (
          <audio
            controls
            src={`/api/works/${id}/media?frame=${selected}&field=audio_path`}
          />
        )}
        <Button
          variant="secondary"
          disabled={disabled}
          onClick={() => open("audio")}
        >
          <RefreshCw size={14} />
          修改文案与配音
        </Button>
      </div>
      <div className="scene-editor-section">
        <h3>
          <Image size={16} />
          画面描述
        </h3>
        <p className="prompt-text">
          {frame.image_prompt || "此分镜使用文字排版或共享画面"}
        </p>
        <Button
          variant="secondary"
          disabled={disabled}
          onClick={() => open("media")}
        >
          <RefreshCw size={14} />
          调整画面
        </Button>
      </div>
      <Accordion title="原始素材">
        {[
          ["image_path", "原始图片"],
          ["video_path", "原始视频"],
          ["video_segment_path", "分镜片段"],
        ].map(
          ([field, label]) =>
            frame[field as keyof Frame] && (
              <a
                key={field}
                className="asset-link"
                href={`/api/works/${id}/media?frame=${selected}&field=${field}`}
                target="_blank"
                rel="noreferrer"
              >
                {label}
                <ArrowUpRight size={15} />
              </a>
            ),
        )}
      </Accordion>
      <button
        className="scene-delete"
        disabled={disabled}
        onClick={() => open("delete")}
      >
        <Trash2 size={15} />
        删除这个分镜
      </button>
    </>
  );
}
function FileTextIcon() {
  return <Clapperboard size={25} />;
}
