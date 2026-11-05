import { useEffect, useRef, useState } from "react";
import { useQuery, useMutation, useQueryClient } from "@tanstack/react-query";
import { useNavigate } from "react-router-dom";
import {
  Sparkles,
  FileText,
  ArrowUpRight,
  ArrowRight,
  Plus,
  Image,
  Music2,
  Play,
  Monitor,
  Smartphone,
  Square,
  Layers,
  SlidersHorizontal,
  Eye,
  Clapperboard,
  Check,
  Volume2,
  RefreshCw,
  Upload,
  Pause,
  LoaderCircle,
} from "lucide-react";
import { useTemplatePreview } from "../hooks/useTemplatePreview";
import { TemplateCover } from "../components/TemplateCover";
import { api, post, active, statusNames } from "../api";
import {
  Button,
  Section,
  Field,
  Accordion,
  Modal,
  ErrorBox,
  Toast,
  Loading,
  Empty,
} from "../components/ui";
import type { Asset, Draft, Models, Job } from "../types";
export const initialDraft: Draft = {
  text: "",
  mode: "generate",
  title: "",
  n_scenes: 5,
  split_mode: "paragraph",
  content_style: "general",
  min_narration_words: 5,
  max_narration_words: 20,
  template_id: "",
  media_workflow: null,
  prompt_prefix: "",
  tts_inference_mode: "local",
  tts_voice: "zh-CN-XiaoxiaoNeural",
  tts_provider: "minimax",
  tts_model: "speech-2.8-turbo",
  tts_voice_id: null,
  tts_speed: 1,
  tts_volume: 1,
  bgm_id: null,
  bgm_volume: 0.2,
  video_fps: 30,
  video_duration: 5,
  first_frame_workflow: null,
  use_narration_audio_as_driving_audio: false,
  template_params: {},
};
const templatesNames: Record<string, string> = {
  image_default: "简约留白",
  image_healing: "治愈手记",
  image_cartoon: "轻松插画",
  image_book: "书页之间",
  image_full: "全屏故事",
  image_film: "电影画幅",
  image_minimal_framed: "框住灵感",
  static_excerpt: "文字摘录",
  image_life_insights: "生活随想",
  image_modern: "现代简约",
  image_elegant: "优雅叙事",
  image_blur_card: "柔焦卡片",
  image_simple_line_drawing: "线条故事",
  image_satirical_cartoon: "幽默漫画",
  video_default: "动态故事",
  video_healing: "动态治愈",
};
export const templateName = (asset: Asset) =>
  templatesNames[asset.name] ||
  asset.name.replace(/^(image|video|static)_/, "").replaceAll("_", " ");
export default function Studio({ onSettings }: { onSettings: () => void }) {
  const navigate = useNavigate();
  const client = useQueryClient();
  const [savedDraft] = useState<Partial<Draft> | null>(() => {
    try {
      return JSON.parse(localStorage.getItem("pixelle-draft") || "null");
    } catch {
      return null;
    }
  });
  const [draft, setDraft] = useState<Draft>(() => ({ ...initialDraft, ...savedDraft }));
  const defaultsApplied = useRef(!!savedDraft);
  const promptPrefixChosen = useRef(
    typeof savedDraft?.prompt_prefix === "string",
  );
  const [batch, setBatch] = useState(false);
  const [topics, setTopics] = useState("");
  const [gallery, setGallery] = useState(false);
  const [orientation, setOrientation] = useState("portrait");
  const [preview, setPreview] = useState<{
    title: string;
    narrations: string[];
    source: string;
  } | null>(null);
  const [previewEditor, setPreviewEditor] = useState(false);
  const [voiceAudio, setVoiceAudio] = useState("");
  const [toast, setToast] = useState("");
  const [mobilePreview, setMobilePreview] = useState(false);
  const [batchId, setBatchId] = useState("");
  const [jobId, setJobId] = useState("");
  const [error, setError] = useState<unknown>();
  const assets = useQuery({
    queryKey: ["resources"],
    queryFn: () => api<Asset[]>("/resources"),
    staleTime: Infinity,
    refetchOnWindowFocus: false,
    refetchOnReconnect: false,
  });
  const models = useQuery({
    queryKey: ["models"],
    queryFn: () => api<Models>("/models"),
  });
  const settings = useQuery({
    queryKey: ["settings"],
    queryFn: () => api<Record<string, any>>("/settings"),
  });
  const defaultPromptPrefix = settings.data?.media?.image?.prompt_prefix;
  const voices = useQuery({
    queryKey: ["voices", draft.tts_inference_mode],
    queryFn: () =>
      api<{ id: string; name: string }[]>(
        "/voices?mode=" + draft.tts_inference_mode,
      ),
    enabled:
      draft.tts_inference_mode === "local" || !!models.data?.tts_api_configured,
  });
  const templates = assets.data?.filter((a) => a.kind === "template") || [];
  const bgms = assets.data?.filter((a) => a.kind === "bgm") || [];
  const template = templates.find((a) => a.id === draft.template_id);
  const templateType = template?.metadata.template_type || "image";
  const selectedModel = models.data?.videos.find(
    (m) => m.key === draft.media_workflow,
  );
  const durationContract = selectedModel?.capabilities?.duration;
  const durations =
    durationContract?.allowed_values ||
    [2, 3, 4, 5, 6, 7, 8, 9, 10, 12, 15].filter(
      (n) =>
        n >= (durationContract?.min || 2) && n <= (durationContract?.max || 15),
    );
  const update = (change: Partial<Draft>) =>
    setDraft((d) => ({ ...d, ...change }));
  useEffect(() => {
    localStorage.setItem("pixelle-draft", JSON.stringify(draft));
  }, [draft]);
  useEffect(() => {
    if (promptPrefixChosen.current || typeof defaultPromptPrefix !== "string") return;
    promptPrefixChosen.current = true;
    update({ prompt_prefix: defaultPromptPrefix });
  }, [defaultPromptPrefix]);
  useEffect(() => {
    if (defaultsApplied.current || !settings.data || !models.data) return;
    defaultsApplied.current = true;
    const cfg = settings.data;
    const provider = cfg.tts_models?.providers?.minimax;
    update({
      tts_inference_mode:
        cfg.tts?.inference_mode === "api" && models.data.tts_api_configured ? "api" : "local",
      tts_voice: cfg.tts?.local?.voice || initialDraft.tts_voice,
      tts_speed: cfg.tts?.local?.speed || initialDraft.tts_speed,
      tts_model: provider?.default_model || initialDraft.tts_model,
      tts_voice_id: provider?.default_voice_id || null,
    });
  }, [settings.data, models.data]);
  useEffect(() => {
    if (
      templates.length &&
      !templates.some((t) => t.id === draft.template_id)
    ) {
      const preferred =
        templates.find(
          (t) =>
            t.metadata.template_path ===
            settings.data?.template?.default_template,
        ) ||
        templates.find(
          (t) =>
            t.name === "image_default" && t.metadata.orientation === "portrait",
        ) ||
        templates[0];
      update({ template_id: preferred.id });
    }
  }, [assets.data, settings.data]);
  useEffect(() => {
    if (models.data) {
      if (!draft.media_workflow && models.data.images.length)
        update({
          media_workflow:
            models.data.images.find(
              (m) => m.key === settings.data?.media?.image?.default_workflow,
            )?.key || models.data.images[0].key,
        });
      if (!draft.first_frame_workflow && models.data.images.length)
        update({ first_frame_workflow: models.data.images[0].key });
    }
  }, [models.data, settings.data]);
  useEffect(() => {
    if (
      templateType === "video" &&
      durations.length &&
      !durations.includes(draft.video_duration)
    )
      update({ video_duration: durations[0] });
  }, [draft.media_workflow, templateType]);
  const currentJob = useQuery({
    queryKey: ["job", jobId],
    queryFn: () => api<Job>("/jobs/" + jobId),
    enabled: !!jobId,
    refetchInterval: (q) =>
      q.state.data && active(q.state.data.status) ? 2000 : false,
  });
  const batchJobs = useQuery({
    queryKey: ["batch", batchId],
    queryFn: () => api<Job[]>("/jobs?batch_id=" + batchId),
    enabled: !!batchId,
    refetchInterval: (q) =>
      q.state.data?.some((j) => active(j.status)) ? 2000 : false,
  });
  const submit = useMutation({
    mutationFn: (body: Draft) => post<Job>("/video/generate", body),
    onSuccess: (job) => {
      setJobId(job.id);
      client.invalidateQueries({ queryKey: ["jobs"] });
      setMobilePreview(true);
      setToast("已开始创作，你可以放心切换页面");
    },
  });
  const script = useMutation({
    mutationFn: () =>
      post<{ title: string; narrations: string[] }>("/preview/script", draft),
    onSuccess: (r) => {
      setPreview({
        ...r,
        source: JSON.stringify({
          text: draft.text,
          mode: draft.mode,
          n_scenes: draft.n_scenes,
          content_style: draft.content_style,
        }),
      });
      setPreviewEditor(true);
    },
  });
  const voice = useMutation({
    mutationFn: () =>
      post<{ url: string }>("/preview/voice", {
        mode: draft.tts_inference_mode,
        voice: draft.tts_voice,
        voice_id: draft.tts_voice_id,
        model: draft.tts_model,
        speed: draft.tts_speed,
      }),
    onSuccess: (r) => setVoiceAudio(r.url),
  });
  const batchSubmit = useMutation({
    mutationFn: () =>
      post<{ batch_id: string }>("/video/batch", {
        topics: topics
          .split("\n")
          .map((t) => t.trim())
          .filter(Boolean),
        settings: { ...draft, text: "批量主题" },
      }),
    onSuccess: (r) => {
      setBatchId(r.batch_id);
      client.invalidateQueries({ queryKey: ["jobs"] });
    },
  });
  const stale =
    !!preview &&
    preview.source !==
      JSON.stringify({
        text: draft.text,
        mode: draft.mode,
        n_scenes: draft.n_scenes,
        content_style: draft.content_style,
      });
  const previewInput = template ? {
    template_id: template.id,
    title: (!stale && preview?.title) || draft.title || "让想法成为作品",
    text: (!stale && preview?.narrations[0]) ||
      (draft.mode === "fixed" && draft.text.trim().split(/\n\s*\n/)[0]) ||
      "从一个灵感开始，讲述属于你的故事。",
    template_params: Object.fromEntries(
      Object.entries(draft.template_params).filter(([key]) => key in (template.metadata.parameters || {})),
    ),
  } : null;
  const personalPreview = !!previewInput && (
    previewInput.title !== "让想法成为作品" ||
    previewInput.text !== "从一个灵感开始，讲述属于你的故事。" ||
    Object.entries(previewInput.template_params).some(([key, value]) => value !== template?.metadata.parameters?.[key]?.default)
  );
  const stylePreview = useTemplatePreview(personalPreview ? previewInput : null, template?.preview_url);
  const generate = () => {
    setError(undefined);
    const body =
      preview && !stale
        ? {
            ...draft,
            mode: "fixed" as const,
            title: preview.title,
            text: preview.narrations.join("\n\n"),
            split_mode: "paragraph" as const,
          }
        : draft;
    submit.mutate(body);
  };
  const selectTemplate = (a: Asset) => {
    const type = a.metadata.template_type || "image";
    const available =
      type === "video" ? models.data?.videos : models.data?.images;
    if (a.id !== draft.template_id)
      update({ template_id: a.id, media_workflow: available?.[0]?.key || null, template_params: {} });
    setOrientation(a.metadata.orientation || "portrait");
    setGallery(false);
  };
  return (
    <div className="studio-page">
      <div className="page-heading">
        <div>
          <h1>
            让想法，成为作品<span className="heading-dot">。</span>
          </h1>
          <p>写内容，选画面与声音，生成视频。</p>
        </div>
        <Button variant="secondary" onClick={() => setBatch(true)}>
          <Layers size={17} />
          批量创作
          <ArrowUpRight size={15} />
        </Button>
      </div>
      <div className="mobile-workspace-tabs">
        <button
          className={!mobilePreview ? "selected" : ""}
          onClick={() => setMobilePreview(false)}
        >
          创作内容
        </button>
        <button
          className={mobilePreview ? "selected" : ""}
          onClick={() => setMobilePreview(true)}
        >
          作品预览{currentJob.data && active(currentJob.data.status) && <i />}
        </button>
      </div>
      <div className={`workspace-grid ${mobilePreview ? "show-preview" : ""}`}>
        <div className="creation-column">
          <Section
            title="创作内容"
            icon={<span className="step-number" aria-hidden="true">1</span>}
          >
            <div className="segmented mode-switch">
              <button
                className={draft.mode === "generate" ? "selected" : ""}
                onClick={() => update({ mode: "generate" })}
              >
                <Sparkles size={16} />
                输入主题
              </button>
              <button
                className={draft.mode === "fixed" ? "selected" : ""}
                onClick={() => update({ mode: "fixed" })}
              >
                <FileText size={16} />
                使用文案
              </button>
            </div>
            <div className="editor-wrap">
              <textarea
                aria-label={draft.mode === "generate" ? "创作主题" : "视频文案"}
                className="idea-editor"
                value={draft.text}
                onChange={(e) => update({ text: e.target.value })}
                placeholder={
                  draft.mode === "generate"
                    ? "输入你想讲的主题，例如：为什么放慢脚步，反而能走得更远。"
                    : "粘贴你的文案，每段文字将成为一个分镜。"
                }
              />
              <div className="editor-footer">
                <span>
                  <i />
                  {draft.mode === "generate"
                    ? "AI 会根据主题生成文案"
                    : "按下方规则拆分文案"}
                </span>
                <span>{draft.text.length} 字</span>
              </div>
            </div>
            {!draft.text && (
              <div className="inspiration">
                <span>试试这些灵感</span>
                {[
                  "生活里的小确幸",
                  "用简单比喻讲懂 AI",
                  "一本书带来的改变",
                ].map((text) => (
                  <button key={text} onClick={() => update({ text })}>
                    {text}
                    <ArrowUpRight size={12} />
                  </button>
                ))}
              </div>
            )}
            <div className="content-options">
              <Field label="作品标题" hint="选填">
                <input
                  placeholder="留空，让 AI 帮你想一个"
                  value={draft.title}
                  onChange={(e) => update({ title: e.target.value })}
                />
              </Field>
              {draft.mode === "generate" ? (
                <Field label="故事长度">
                  <select
                    value={draft.n_scenes}
                    onChange={(e) => update({ n_scenes: +e.target.value })}
                  >
                    {[3, 5, 8, 10, 15, 20, 30].map((n) => (
                      <option key={n} value={n}>
                        {n} 个分镜{n === 5 ? " · 轻量表达" : ""}
                      </option>
                    ))}
                  </select>
                </Field>
              ) : (
                <Field label="文案拆分">
                  <select
                    value={draft.split_mode}
                    onChange={(e) =>
                      update({
                        split_mode: e.target.value as Draft["split_mode"],
                      })
                    }
                  >
                    <option value="paragraph">按段落</option>
                    <option value="line">按行</option>
                    <option value="sentence">按句子</option>
                  </select>
                </Field>
              )}
            </div>
            <div className="script-row">
              <span>生成前可以先查看、修改文案</span>
              <Button
                variant="ghost"
                disabled={!draft.text.trim()}
                busy={script.isPending}
                onClick={() => script.mutate()}
              >
                <Eye size={16} />
                预览文案
              </Button>
            </div>
            {preview && (
              <div className={`script-ready ${stale ? "stale" : ""}`}>
                <span>
                  <Check size={16} />
                  {stale
                    ? "内容有变化，请重新预览"
                    : `文案已准备好 · ${preview.narrations.length} 段`}
                </span>
                <button onClick={() => setPreviewEditor(true)}>
                  编辑文案 <ArrowRight size={14} />
                </button>
              </div>
            )}
            <ErrorBox error={script.error} />
          </Section>
          <Section
            title="画面与风格"
            icon={<span className="step-number" aria-hidden="true">2</span>}
            action={
              <div className="style-heading-actions">
                <button
                  className="text-button mobile-preview-link"
                  onClick={() => setMobilePreview(true)}
                >
                  <Eye size={16} />查看预览
                </button>
                <Button variant="secondary" onClick={() => setGallery(true)}>
                  更换风格
                </Button>
              </div>
            }
          >
            {templateType === "static" && (
              <p className="muted">纯文字模板无需选择画面模型。</p>
            )}
            {templateType !== "static" && (
              <Field
                label={templateType === "video" ? "动态画面模型" : "画面模型"}
              >
                <select
                  value={draft.media_workflow || ""}
                  onChange={(e) =>
                    update({ media_workflow: e.target.value || null })
                  }
                >
                  <option value="">选择可用模型</option>
                  {(templateType === "video"
                    ? models.data?.videos
                    : models.data?.images
                  )?.map((m) => (
                    <option value={m.key} key={m.key}>
                      {m.name} · {m.provider}
                    </option>
                  ))}
                </select>
              </Field>
            )}
            {templateType === "video" && (
              <>
                <Field label="首帧图片模型">
                  <select
                    value={draft.first_frame_workflow || ""}
                    onChange={(e) =>
                      update({ first_frame_workflow: e.target.value })
                    }
                  >
                    {models.data?.images.map((m) => (
                      <option key={m.key} value={m.key}>
                        {m.name}
                      </option>
                    ))}
                  </select>
                </Field>
                <Field label="每段动态时长">
                  <select
                    value={draft.video_duration}
                    onChange={(e) =>
                      update({ video_duration: +e.target.value })
                    }
                  >
                    {durations.map((n) => (
                      <option key={n} value={n}>
                        {n} 秒
                      </option>
                    ))}
                  </select>
                </Field>
              </>
            )}
            {!models.data?.images.length && templateType === "image" && (
              <div className="inline-hint">
                还没有可用的画面模型。
                <button onClick={onSettings}>前往设置</button>
              </div>
            )}
            <ErrorBox error={stylePreview.error} />
            {stylePreview.error && <button className="text-button" onClick={stylePreview.retry}>重新加载预览</button>}
          </Section>
          <Section
            title="配音与音乐"
            icon={<span className="step-number" aria-hidden="true">3</span>}
          >
            <div className="sound-grid">
              <div>
                <div className="label-action">
                  <span className="field-label">旁白配音</span>
                  <select
                    className="compact-select"
                    aria-label="配音服务"
                    value={draft.tts_inference_mode}
                    onChange={(e) =>
                      update({
                        tts_inference_mode: e.target
                          .value as Draft["tts_inference_mode"],
                      })
                    }
                  >
                    <option value="local">Edge TTS</option>
                    <option
                      value="api"
                      disabled={!models.data?.tts_api_configured}
                    >
                      MiniMax
                    </option>
                  </select>
                </div>
                <div className="voice-choice">
                  <div className="voice-icon">
                    <Volume2 size={19} />
                  </div>
                  <select
                    aria-label="配音音色"
                    value={
                      draft.tts_inference_mode === "local"
                        ? draft.tts_voice
                        : draft.tts_voice_id || ""
                    }
                    onChange={(e) =>
                      update(
                        draft.tts_inference_mode === "local"
                          ? { tts_voice: e.target.value }
                          : { tts_voice_id: e.target.value },
                      )
                    }
                  >
                    <option value="">选择音色</option>
                    {voices.data?.map((v) => (
                      <option key={v.id} value={v.id}>
                        {v.name}
                      </option>
                    ))}
                  </select>
                  <button
                    className="icon-button"
                    aria-label="试听配音"
                    disabled={voice.isPending}
                    onClick={() => voice.mutate()}
                  >
                    <Play size={16} />
                  </button>
                </div>
                <div className="range-row">
                  <span>语速</span>
                  <input
                    aria-label="配音语速"
                    type="range"
                    min="0.5"
                    max="2"
                    step="0.1"
                    value={draft.tts_speed}
                    onChange={(e) => update({ tts_speed: +e.target.value })}
                  />
                  <strong>{draft.tts_speed.toFixed(1)}×</strong>
                </div>
                {voiceAudio && <audio controls autoPlay src={voiceAudio} />}
                <ErrorBox error={voice.error} />
                <ErrorBox error={voices.error} />
              </div>
              <div>
                <div className="label-action">
                  <span className="field-label">背景音乐</span>
                  <label className="text-button upload-label">
                    <Upload size={13} />
                    上传
                    <input
                      type="file"
                      accept="audio/mpeg,audio/wav"
                      onChange={async (e) => {
                        const file = e.target.files?.[0];
                        if (!file) return;
                        try {
                          const result = await post<{
                            id: string;
                            url: string;
                            headers: Record<string, string>;
                          }>("/uploads", {
                            kind: "bgm",
                            name: file.name,
                            size: file.size,
                            content_type: file.type || "audio/mpeg",
                          });
                          const response = await fetch(result.url, {
                            method: "PUT",
                            headers: {
                              "Content-Type": file.type || "audio/mpeg",
                              ...result.headers,
                            },
                            body: file,
                          });
                          if (!response.ok) throw new Error("音乐上传失败");
                          await post("/uploads/" + result.id + "/confirm");
                          client.invalidateQueries({ queryKey: ["resources"] });
                          update({ bgm_id: result.id });
                        } catch (e) {
                          setError(e);
                        }
                      }}
                    />
                  </label>
                </div>
                <div className="voice-choice">
                  <div className="music-icon">
                    <Music2 size={18} />
                  </div>
                  <select
                    aria-label="背景音乐"
                    value={draft.bgm_id || ""}
                    onChange={(e) => update({ bgm_id: e.target.value || null })}
                  >
                    <option value="">不添加音乐</option>
                    {bgms.map((a) => (
                      <option key={a.id} value={a.id}>
                        {a.name}
                      </option>
                    ))}
                  </select>
                  {draft.bgm_id && (
                    <button
                      className="icon-button"
                      aria-label="试听音乐"
                      onClick={async () => {
                        try {
                          const r = await api<{ url: string }>(
                            "/resources/" + draft.bgm_id + "/url",
                          );
                          setVoiceAudio(r.url);
                        } catch (e) {
                          setError(e);
                        }
                      }}
                    >
                      <Play size={16} />
                    </button>
                  )}
                </div>
                <div className="range-row">
                  <span>音量</span>
                  <input
                    aria-label="背景音乐音量"
                    type="range"
                    min="0"
                    max="1"
                    step="0.05"
                    value={draft.bgm_volume}
                    onChange={(e) => update({ bgm_volume: +e.target.value })}
                  />
                  <strong>{Math.round(draft.bgm_volume * 100)}%</strong>
                </div>
              </div>
            </div>
          </Section>
          <Accordion title="更多创作选项">
            <div className="form-grid">
              <Field label="文案风格">
                <select
                  value={draft.content_style}
                  onChange={(e) =>
                    update({
                      content_style: e.target.value as Draft["content_style"],
                    })
                  }
                >
                  <option value="general">生活与故事</option>
                  <option value="tech_pop">科技科普</option>
                </select>
              </Field>
              <Field label="视频帧率">
                <select
                  value={draft.video_fps}
                  onChange={(e) => update({ video_fps: +e.target.value })}
                >
                  <option value={30}>30 FPS</option>
                  <option value={60}>60 FPS</option>
                </select>
              </Field>
              <Field label="每段最少字数">
                <input
                  type="number"
                  min={1}
                  max={100}
                  value={draft.min_narration_words}
                  onChange={(e) =>
                    update({ min_narration_words: +e.target.value })
                  }
                />
              </Field>
              <Field label="每段最多字数">
                <input
                  type="number"
                  min={1}
                  max={200}
                  value={draft.max_narration_words}
                  onChange={(e) =>
                    update({ max_narration_words: +e.target.value })
                  }
                />
              </Field>
            </div>
            <Field label="画面风格描述" hint="Prompt 前缀">
              <textarea
                rows={3}
                placeholder="补充你的画面偏好，如：自然光、温暖色调、简约插画"
                value={draft.prompt_prefix}
                onChange={(e) => {
                  promptPrefixChosen.current = true;
                  update({ prompt_prefix: e.target.value });
                }}
              />
            </Field>
            <div className="inline-hint">
              <span>用于 AI 画面生成；模板效果图展示排版。</span>
              <button
                type="button"
                disabled={typeof defaultPromptPrefix !== "string"}
                onClick={() => {
                  promptPrefixChosen.current = true;
                  update({ prompt_prefix: defaultPromptPrefix });
                }}
              >
                使用系统默认风格
              </button>
            </div>
            {templateType === "video" && (
              <label className="checkbox-row">
                <input
                  type="checkbox"
                  checked={draft.use_narration_audio_as_driving_audio}
                  onChange={(e) =>
                    update({
                      use_narration_audio_as_driving_audio: e.target.checked,
                    })
                  }
                />
                使用旁白音频驱动画面（需模型支持）
              </label>
            )}
            {Object.entries(template?.metadata.parameters || {}).length > 0 && (
              <div className="template-fields">
                <p className="muted">调整模板细节</p>
                {Object.entries(template?.metadata.parameters || {}).map(
                  ([key, param]) => (
                    <Field key={key} label={parameterLabel(key, param.label)}>
                      {param.type === "bool" ? (
                        <select
                          value={String(
                            draft.template_params[key] ??
                              param.default ??
                              false,
                          )}
                          onChange={(e) =>
                            update({
                              template_params: {
                                ...draft.template_params,
                                [key]: e.target.value === "true",
                              },
                            })
                          }
                        >
                          <option value="true">显示</option>
                          <option value="false">隐藏</option>
                        </select>
                      ) : (
                        <input
                          type={
                            param.type === "color"
                              ? "color"
                              : param.type === "number"
                                ? "number"
                                : "text"
                          }
                          value={
                            draft.template_params[key] ?? param.default ?? ""
                          }
                          onChange={(e) =>
                            update({
                              template_params: {
                                ...draft.template_params,
                                [key]:
                                  param.type === "number"
                                    ? +e.target.value
                                    : e.target.value,
                              },
                            })
                          }
                        />
                      )}
                    </Field>
                  ),
                )}
              </div>
            )}
          </Accordion>
          <ErrorBox
            error={error || submit.error || assets.error || models.error}
          />
          <div className="generate-bar">
            <span>
              <Sparkles size={15} />
              {draft.text.trim() ? "内容已填写，可以生成视频" : "填写主题或文案后即可生成"}
            </span>
            <Button
              busy={submit.isPending}
              disabled={
                !draft.text.trim() ||
                !template ||
                (currentJob.data && active(currentJob.data.status)) ||
                stale
              }
              onClick={generate}
            >
              <Sparkles size={17} />
              生成视频
              <ArrowRight size={17} />
            </Button>
          </div>
        </div>
        <aside className="preview-column">
          <div className="preview-panel">
            <div className="preview-title">
              <h2>作品预览</h2>
              <span className="preview-live-status" role="status">
                {stylePreview.updating ? <><LoaderCircle size={13} className="spin" />正在更新</> : personalPreview ? "个人预览" : "风格效果图"}
              </span>
            </div>
            <div
              className={`preview-stage ${template?.metadata.orientation || "portrait"}`}
              aria-busy={stylePreview.updating}
            >
              {currentJob.data?.status === "completed" ? (
                <video
                  src={`/api/works/${currentJob.data.work_id}/media`}
                  controls
                  playsInline
                />
              ) : stylePreview.url ? (
                <img src={stylePreview.url} alt="当前模板真实渲染预览" />
              ) : template ? (
                <div className="preview-first-load">
                  {stylePreview.updating ? <LoaderCircle size={26} className="spin" /> : <Eye size={26} />}
                  <span>{stylePreview.updating ? "正在生成个人预览…" : "风格效果图暂时不可用"}</span>
                </div>
              ) : (
                <div className="preview-paper">
                  <span className="paper-corner" />
                  <div className="preview-art">
                    <div className="preview-orbit" />
                    <div className="preview-leaf leaf-one" />
                    <div className="preview-leaf leaf-two" />
                    <div className="preview-book">
                      <span />
                      <span />
                      <span />
                    </div>
                    <div className="preview-star">✦</div>
                  </div>
                  <span className="preview-caption">
                    A LITTLE IDEA, A NEW STORY.
                  </span>
                  <h3>
                    你的下一个
                    <br />
                    好故事，从这里开始。
                  </h3>
                  <div className="paper-lines">
                    <i />
                    <i />
                    <i />
                  </div>
                </div>
              )}
              {stylePreview.updating && currentJob.data?.status !== "completed" && (
                <div className="preview-updating"><LoaderCircle size={15} className="spin" />正在更新风格</div>
              )}
            </div>
            {stylePreview.error && <div className="preview-error"><ErrorBox error={stylePreview.error} /><button className="text-button" onClick={stylePreview.retry}>重新加载预览</button></div>}
            {currentJob.data && (
              <div className="job-progress">
                <div>
                  <strong>
                    {currentJob.data.message ||
                      statusNames[currentJob.data.status]}
                  </strong>
                  <span>{Math.round(currentJob.data.progress * 100)}%</span>
                </div>
                <progress value={currentJob.data.progress} max={1} />
                <ErrorBox error={currentJob.data.error} />
                <div className="job-actions">
                  {active(currentJob.data.status) ? (
                    <Button
                      variant="ghost"
                      onClick={() => post("/jobs/" + jobId + "/cancel")}
                    >
                      取消任务
                    </Button>
                  ) : (
                    <Button
                      variant="secondary"
                      onClick={() =>
                        navigate("/works/" + currentJob.data!.work_id)
                      }
                    >
                      查看作品
                      <ArrowUpRight size={15} />
                    </Button>
                  )}
                </div>
              </div>
            )}
            <div className="preview-details">
              <div>
                <span>当前风格</span>
                <strong>
                  {template ? templateName(template) : "等待选择"}
                </strong>
              </div>
              <div>
                <span>画幅</span>
                <strong>
                  {template?.metadata.width && template?.metadata.height
                    ? `${template.metadata.width} × ${template.metadata.height}`
                    : "9:16"}
                </strong>
              </div>
              <div>
                <span>内容形式</span>
                <strong>
                  {templateType === "video"
                    ? "动态视频"
                    : templateType === "static"
                      ? "文字叙事"
                      : "图文叙事"}
                </strong>
              </div>
            </div>
          </div>
          <div className="autosave">
            <Check size={13} />
            草稿已自动保存到此浏览器
          </div>
        </aside>
      </div>
      {gallery && (
        <Modal
          title="选一种风格，讲你的故事"
          wide
          onClose={() => setGallery(false)}
        >
          <div className="gallery-tabs">
            {[
              ["portrait", "竖屏", Smartphone],
              ["landscape", "横屏", Monitor],
              ["square", "方形", Square],
            ].map(([value, label, Icon]) => (
              <button
                key={String(value)}
                className={orientation === value ? "selected" : ""}
                onClick={() => setOrientation(String(value))}
              >
                {typeof Icon !== "string" && <Icon size={16} />} {String(label)}
              </button>
            ))}
          </div>
          {!templates.length ? (
            <Empty icon={<Image size={30} />} title="还没有模板">
              完成 COS 配置后，运行资源初始化命令导入模板。
            </Empty>
          ) : (
            <div className="template-gallery">
              {templates
                .filter((a) => a.metadata.orientation === orientation)
                .map((a) => (
                  <button
                    className={`template-option ${draft.template_id === a.id ? "chosen" : ""}`}
                    key={a.id}
                    onClick={() => selectTemplate(a)}
                  >
                    <TemplateCover asset={a} />
                    <strong>{templateName(a)}</strong>
                    <span>
                      {a.metadata.template_type === "video"
                        ? "动态视频"
                        : a.metadata.template_type === "static"
                          ? "文字叙事"
                          : "图文叙事"}
                    </span>
                    {draft.template_id === a.id && (
                      <i>
                        <Check size={14} />
                      </i>
                    )}
                  </button>
                ))}
            </div>
          )}
        </Modal>
      )}
      {previewEditor && preview && (
        <Modal
          title="让文案更像你"
          wide
          onClose={() => setPreviewEditor(false)}
        >
          <Field label="作品标题">
            <input
              value={preview.title}
              onChange={(e) =>
                setPreview({ ...preview, title: e.target.value })
              }
            />
          </Field>
          {preview.narrations.map((text, index) => (
            <Field
              key={index}
              label={`分镜 ${String(index + 1).padStart(2, "0")}`}
            >
              <textarea
                rows={3}
                value={text}
                onChange={(e) =>
                  setPreview({
                    ...preview,
                    narrations: preview.narrations.map((t, i) =>
                      i === index ? e.target.value : t,
                    ),
                  })
                }
              />
            </Field>
          ))}
          <Button onClick={() => setPreviewEditor(false)}>
            保存文案
            <Check size={16} />
          </Button>
        </Modal>
      )}
      {batch && (
        <Modal
          title="批量创作，一次收集更多灵感"
          wide
          onClose={() => setBatch(false)}
        >
          <p className="muted">
            每行一个主题，最多 100 个。使用当前选择的风格和声音。
          </p>
          <Field label="创作主题">
            <textarea
              rows={8}
              value={topics}
              onChange={(e) => setTopics(e.target.value)}
              placeholder="生活里的小确幸\n用简单比喻讲懂 AI\n一本书带来的改变"
            />
          </Field>
          <div className="batch-summary">
            <span>
              {topics.split("\n").filter((t) => t.trim()).length} 个主题
            </span>
            <strong>
              {template ? templateName(template) : "请先选择风格"}
            </strong>
          </div>
          <Button
            busy={batchSubmit.isPending}
            disabled={
              !topics.trim() ||
              topics.split("\n").filter((t) => t.trim()).length > 100 ||
              !template
            }
            onClick={() => batchSubmit.mutate()}
          >
            开始批量创作
            <ArrowRight size={16} />
          </Button>
          <ErrorBox error={batchSubmit.error} />
          {batchJobs.data?.map((job) => (
            <div className="batch-item" key={job.id}>
              <div>
                <strong>{job.params.text}</strong>
                <span>{statusNames[job.status]}</span>
              </div>
              <progress max={1} value={job.progress} />
              <ErrorBox error={job.error} />
              {active(job.status) ? (
                <button
                  className="text-button"
                  onClick={() => post("/jobs/" + job.id + "/cancel")}
                >
                  取消
                </button>
              ) : job.status === "completed" ? (
                <button
                  className="text-button"
                  onClick={() => navigate("/works/" + job.work_id)}
                >
                  查看作品
                </button>
              ) : (
                <button
                  className="text-button"
                  onClick={async () => {
                    try {
                      await post("/jobs/" + job.id + "/retry");
                      client.invalidateQueries({ queryKey: ["jobs"] });
                    } catch (e) {
                      setError(e);
                    }
                  }}
                >
                  重试
                </button>
              )}
            </div>
          ))}
        </Modal>
      )}
      {toast && <Toast message={toast} onClose={() => setToast("")} />}
    </div>
  );
}
function parameterLabel(key: string, label: string) {
  return (
    (
      {
        bg_color: "背景颜色",
        text_color: "文字颜色",
        title_color: "标题颜色",
        font_size: "文字大小",
        title_font_size: "标题大小",
        show_title: "显示标题",
        author: "作者",
        subtitle: "副标题",
      } as Record<string, string>
    )[key] || label.replaceAll("_", " ")
  );
}
