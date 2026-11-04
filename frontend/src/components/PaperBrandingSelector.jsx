import React, { useEffect, useState } from "react";
import { api } from "@/lib/api";
import { toast } from "sonner";

export default function PaperBrandingSelector({ paper, onPaperChange }) {
  const [presets, setPresets] = useState([]);
  const [applying, setApplying] = useState(false);

  useEffect(() => {
    let active = true;
    api.get("/branding-presets")
      .then(({ data }) => { if (active) setPresets(data); })
      .catch(() => {});
    return () => { active = false; };
  }, [paper.id]);

  const hasBranding = Boolean(
    paper.branding_id || paper.school_name || paper.header_image_path
    || paper.branding_tagline || paper.branding_footer || paper.branding_watermark
  );
  const selection = hasBranding ? (paper.branding_id || "__saved_branding__") : "";
  const selectedPresetMissing = hasBranding && !presets.some(preset => preset.id === paper.branding_id);

  const changeBranding = async (brandingId) => {
    setApplying(true);
    try {
      const { data } = brandingId
        ? await api.post(`/papers/${paper.id}/branding/apply`, { branding_id: brandingId })
        : await api.delete(`/papers/${paper.id}/branding`);
      onPaperChange(data);
      toast.success(brandingId ? "Branding applied to this paper" : "Branding removed from this paper");
    } catch (err) {
      toast.error(err?.response?.data?.detail || (brandingId ? "Could not apply branding" : "Could not remove branding"));
    } finally {
      setApplying(false);
    }
  };

  return <div className="flex items-center gap-2 border-2 border-black px-3" data-testid="paper-branding-status">
    <label htmlFor="paper-branding-select" className="text-sm font-medium whitespace-nowrap">Branding</label>
    <select
      id="paper-branding-select"
      value={selection}
      onChange={(event) => changeBranding(event.target.value)}
      disabled={applying}
      className="min-h-9 max-w-40 bg-white text-sm"
      data-testid="paper-branding-select"
    >
      <option value="">None</option>
      {presets.map((preset) => <option key={preset.id} value={preset.id}>{preset.name}</option>)}
      {selectedPresetMissing && <option value={selection}>{paper.branding_name || "Saved branding"}</option>}
    </select>
  </div>;
}
