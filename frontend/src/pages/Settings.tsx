import { useEffect, useState } from "react";
import { useQuery, useQueryClient } from "@tanstack/react-query";
import {
  Settings,
  KeyRound,
  Image,
  Mic,
  Check,
  ShieldCheck,
  Eye,
  EyeOff,
} from "lucide-react";
import { api } from "../api";
import {
  Button,
  Field,
  Modal,
  ErrorBox,
  Loading,
  Toast,
} from "../components/ui";
const providers: Record<string, string> = {
  openai: "OpenAI",
  openrouter: "OpenRouter",
  dashscope: "阿里云百炼",
  deepseek: "DeepSeek",
  gemini: "Google Gemini",
  ark: "火山方舟",
  hunyuan: "腾讯混元",
  kling: "可灵",
};
export default function SettingsPanel({ onClose }: { onClose: () => void }) {
  const client = useQueryClient();
  const query = useQuery({
    queryKey: ["settings"],
    queryFn: () => api<Record<string, any>>("/settings"),
  });
  const [cfg, setCfg] = useState<Record<string, any>>({});
  const [tab, setTab] = useState("llm");
  const [provider, setProvider] = useState("dashscope");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<unknown>();
  const [saved, setSaved] = useState(false);
  useEffect(() => {
    if (query.data) setCfg(structuredClone(query.data));
  }, [query.data]);
  const update = (path: string[], value: unknown) =>
    setCfg((c) => {
      const n = structuredClone(c);
      let node = n;
      for (const key of path.slice(0, -1)) node = node[key] ??= {};
      node[path.at(-1)!] = value;
      return n;
    });
  const save = async () => {
    setBusy(true);
    setError(undefined);
    try {
      await api("/settings", { method: "PATCH", body: JSON.stringify(cfg) });
      await Promise.all([
        client.invalidateQueries({ queryKey: ["settings"] }),
        client.invalidateQueries({ queryKey: ["models"] }),
        client.invalidateQueries({ queryKey: ["voices"] }),
      ]);
      setSaved(true);
    } catch (e) {
      setError(e);
    } finally {
      setBusy(false);
    }
  };
  return (
    <Modal title="让创作更顺手" wide onClose={onClose}>
      <p className="muted settings-intro">
        配置你习惯的模型和声音。已保存的密钥会隐藏显示。
      </p>
      <div className="settings-layout">
        <nav className="settings-nav">
          {[
            ["llm", "文案模型", KeyRound],
            ["media", "画面模型", Image],
            ["tts", "配音模型", Mic],
          ].map(([value, label, Icon]) => (
            <button
              key={String(value)}
              className={tab === value ? "selected" : ""}
              onClick={() => setTab(String(value))}
            >
              {typeof Icon !== "string" && <Icon size={17} />} {String(label)}
            </button>
          ))}
        </nav>
        <div className="settings-content">
          {query.isPending ? (
            <Loading />
          ) : tab === "llm" ? (
            <>
              <h3>文案创作模型</h3>
              <p className="muted">支持 OpenAI 兼容接口，沿用你熟悉的服务。</p>
              <Field label="API 地址">
                <input
                  type="url"
                  value={cfg.llm?.base_url || ""}
                  placeholder="https://api.deepseek.com"
                  onChange={(e) => update(["llm", "base_url"], e.target.value)}
                />
              </Field>
              <Field label="模型名称">
                <input
                  value={cfg.llm?.model || ""}
                  placeholder="输入服务商提供的模型名称"
                  onChange={(e) => update(["llm", "model"], e.target.value)}
                />
              </Field>
              <Field label="API Key">
                <SecretInput
                  value={cfg.llm?.api_key || ""}
                  onChange={(v) => update(["llm", "api_key"], v)}
                />
              </Field>
            </>
          ) : tab === "media" ? (
            <>
              <h3>画面与视频服务</h3>
              <Field label="选择服务商">
                <select
                  value={provider}
                  onChange={(e) => setProvider(e.target.value)}
                >
                  {Object.entries(providers).map(([id, name]) => (
                    <option key={id} value={id}>
                      {name}
                    </option>
                  ))}
                </select>
              </Field>
              <Field label="API 地址">
                <input
                  type="url"
                  value={cfg.api_providers?.[provider]?.base_url || ""}
                  placeholder="服务商 API 地址"
                  onChange={(e) =>
                    update(
                      ["api_providers", provider, "base_url"],
                      e.target.value,
                    )
                  }
                />
              </Field>
              {provider === "kling" ? (
                <>
                  <Field label="Access Key">
                    <SecretInput
                      value={cfg.api_providers?.kling?.access_key || ""}
                      onChange={(v) =>
                        update(["api_providers", "kling", "access_key"], v)
                      }
                    />
                  </Field>
                  <Field label="Secret Key">
                    <SecretInput
                      value={cfg.api_providers?.kling?.secret_key || ""}
                      onChange={(v) =>
                        update(["api_providers", "kling", "secret_key"], v)
                      }
                    />
                  </Field>
                </>
              ) : (
                <Field label="API Key">
                  <SecretInput
                    value={cfg.api_providers?.[provider]?.api_key || ""}
                    onChange={(v) =>
                      update(["api_providers", provider, "api_key"], v)
                    }
                  />
                </Field>
              )}
              <label className="checkbox-row">
                <input
                  type="checkbox"
                  checked={cfg.api_providers?.[provider]?.use_proxy || false}
                  onChange={(e) =>
                    update(
                      ["api_providers", provider, "use_proxy"],
                      e.target.checked,
                    )
                  }
                />
                通过代理访问此服务
              </label>
              <Field label="代理地址" hint="选填">
                <input
                  value={cfg.api_providers?.common?.local_proxy || ""}
                  placeholder="http://127.0.0.1:7890"
                  onChange={(e) =>
                    update(
                      ["api_providers", "common", "local_proxy"],
                      e.target.value,
                    )
                  }
                />
              </Field>
              <div className="inline-hint">
                配置完成后，创作页会显示此服务支持的可用模型。
              </div>
            </>
          ) : (
            <>
              <h3>MiniMax 配音</h3>
              <p className="muted">
                Edge TTS 无需 API Key；配置 MiniMax 后可使用更多音色。
              </p>
              <Field label="API 地址">
                <input
                  type="url"
                  value={cfg.tts_models?.providers?.minimax?.base_url || ""}
                  onChange={(e) =>
                    update(
                      ["tts_models", "providers", "minimax", "base_url"],
                      e.target.value,
                    )
                  }
                />
              </Field>
              <Field label="API Key">
                <SecretInput
                  value={cfg.tts_models?.providers?.minimax?.api_key || ""}
                  onChange={(v) =>
                    update(["tts_models", "providers", "minimax", "api_key"], v)
                  }
                />
              </Field>
              <Field label="默认模型">
                <input
                  value={cfg.tts_models?.default_model || "speech-2.8-turbo"}
                  onChange={(e) => {
                    update(["tts_models", "default_model"], e.target.value);
                    update(
                      ["tts_models", "providers", "minimax", "default_model"],
                      e.target.value,
                    );
                  }}
                />
              </Field>
              <Field label="默认音色 ID" hint="选填">
                <input
                  value={
                    cfg.tts_models?.providers?.minimax?.default_voice_id || ""
                  }
                  onChange={(e) =>
                    update(
                      [
                        "tts_models",
                        "providers",
                        "minimax",
                        "default_voice_id",
                      ],
                      e.target.value,
                    )
                  }
                />
              </Field>
            </>
          )}
        </div>
      </div>
      <ErrorBox error={error || query.error} />
      <div className="settings-footer">
        <span>
          <ShieldCheck size={15} />
          密钥仅保存在服务器
        </span>
        <Button busy={busy} onClick={save}>
          <Check size={17} />
          保存设置
        </Button>
      </div>
      {saved && <Toast message="设置已保存" onClose={() => setSaved(false)} />}
    </Modal>
  );
}
function SecretInput({
  value,
  onChange,
}: {
  value: string;
  onChange: (value: string) => void;
}) {
  const [show, setShow] = useState(false);
  return (
    <div className="secret-input">
      <input
        type={show ? "text" : "password"}
        autoComplete="new-password"
        value={value}
        onFocus={(e) => {
          if (value === "••••••••") e.currentTarget.select();
        }}
        onChange={(e) => onChange(e.target.value)}
        placeholder="输入 API Key"
      />
      <button
        className="icon-button"
        aria-label={show ? "隐藏密钥" : "显示密钥"}
        type="button"
        onClick={() => setShow(!show)}
      >
        {show ? <EyeOff size={17} /> : <Eye size={17} />}
      </button>
    </div>
  );
}
