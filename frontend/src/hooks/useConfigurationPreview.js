import { useEffect, useState } from "react";
import { api } from "../lib/api";

export default function useConfigurationPreview(requestKey) {
  const [state, setState] = useState({ preview: null, loading: true, error: "" });
  useEffect(() => {
    const controller = new AbortController();
    let cancelled = false;
    setState({ preview: null, loading: true, error: "" });
    const timer = setTimeout(async () => {
      try {
        const { data } = await api.post("/papers/configuration-preview", JSON.parse(requestKey), { signal: controller.signal });
        if (!cancelled) setState({ preview: { ...data, requestKey }, loading: false, error: "" });
      } catch (err) {
        if (!cancelled) {
          const detail = err?.response?.data?.detail;
          setState({ preview: null, loading: false, error: typeof detail === "string" ? detail : "Complete valid paper settings to refresh the backend summary." });
        }
      }
    }, 650);
    return () => { cancelled = true; clearTimeout(timer); controller.abort(); };
  }, [requestKey]);
  return state;
}
