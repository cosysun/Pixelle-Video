import { useEffect, useState } from "react";
import { keepPreviousData, useQuery } from "@tanstack/react-query";
import { api } from "../api";

type PreviewInput = {
  template_id: string;
  title: string;
  text: string;
  template_params: Record<string, unknown>;
};

export function useTemplatePreview(input: PreviewInput | null, coverUrl = "") {
  const requested = input ? JSON.stringify(input) : "";
  const [settled, setSettled] = useState("");
  useEffect(() => {
    const timer = window.setTimeout(() => setSettled(requested), 450);
    return () => window.clearTimeout(timer);
  }, [requested]);
  const preview = useQuery({
    queryKey: ["template-preview", settled],
    queryFn: ({ signal }) =>
      api<{ url: string }>("/preview/template", {
        method: "POST",
        body: settled,
        signal,
      }),
    enabled: !!requested && !!settled,
    placeholderData: keepPreviousData,
    staleTime: Infinity,
    gcTime: 10 * 60 * 1000,
    refetchOnWindowFocus: false,
    refetchOnReconnect: false,
    retry: false,
  });
  return {
    url: requested ? preview.data?.url || coverUrl : coverUrl,
    updating: !!requested && (requested !== settled || preview.isFetching),
    error: requested === settled ? preview.error : null,
    retry: () => preview.refetch(),
  };
}
