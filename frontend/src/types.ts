export type Asset = {
  id: string;
  kind: string;
  name: string;
  size: number;
  preview_url?: string;
  metadata: {
    template_path?: string;
    template_type?: string;
    orientation?: string;
    width?: number;
    height?: number;
    preview_id?: string;
    parameters?: Record<string, { type: string; default: any; label: string }>;
  };
};
export type Model = {
  key: string;
  name: string;
  provider: string;
  model: string;
  capabilities?: {
    duration?: { min?: number; max?: number; allowed_values?: number[] };
    adapter_ability_types?: string[];
  };
  adapter_ability_types?: string[];
};
export type Models = {
  images: Model[];
  videos: Model[];
  tts_api_configured: boolean;
  llm_configured: boolean;
};
export type Job = {
  id: string;
  kind: string;
  work_id: string;
  batch_id?: string;
  status: string;
  progress: number;
  message: string;
  error?: string;
  params: Record<string, any>;
  result?: { work_id?: string };
  created_at: string;
};
export type Work = {
  id: string;
  task_id: string;
  kind: string;
  title: string;
  status: string;
  created_at: string;
  duration: number;
  n_frames: number;
  video_path?: string;
  cover?: string;
  error?: string;
};
export type Frame = {
  index: number;
  narration: string;
  image_prompt: string;
  image_path?: string;
  composed_image_path?: string;
  video_path?: string;
  audio_path?: string;
  video_segment_path?: string;
  image_source_index?: number;
  duration: number;
};
export type Detail = {
  metadata: {
    input: Record<string, any>;
    status: string;
    result?: { video_path?: string };
    error?: string;
  };
  storyboard?: { title: string; frames: Frame[]; config: Record<string, any> };
};
export type Draft = {
  text: string;
  mode: "generate" | "fixed";
  title: string;
  n_scenes: number;
  split_mode: "paragraph" | "line" | "sentence";
  content_style: "general" | "tech_pop";
  min_narration_words: number;
  max_narration_words: number;
  template_id: string;
  media_workflow: string | null;
  prompt_prefix: string;
  tts_inference_mode: "local" | "api";
  tts_voice: string;
  tts_provider: "minimax";
  tts_model: string;
  tts_voice_id: string | null;
  tts_speed: number;
  tts_volume: number;
  bgm_id: string | null;
  bgm_volume: number;
  video_fps: number;
  video_duration: number;
  first_frame_workflow: string | null;
  use_narration_audio_as_driving_audio: boolean;
  template_params: Record<string, any>;
};
