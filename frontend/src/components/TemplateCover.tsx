import { useState } from "react";
import { Image } from "lucide-react";
import type { Asset } from "../types";

export function TemplateCover({ asset }: { asset?: Asset }) {
  const [failedUrl, setFailedUrl] = useState<string>();
  const url = asset?.preview_url;
  return (
    <div className={`template-cover ${asset?.metadata.orientation || "portrait"}`}>
      {url && url !== failedUrl ? (
        <img src={url} alt={`${asset?.name || "模板"} 实际效果`} loading="lazy" onError={() => setFailedUrl(url)} />
      ) : (
        <div className="template-cover-missing"><Image size={24} /><span>效果图暂不可用</span></div>
      )}
    </div>
  );
}
