import React, { useEffect, useState } from "react";
import Header from "@/components/Header";
import { api } from "@/lib/api";
import { toast } from "sonner";
import { useConfirmation } from "@/context/ConfirmationContext";
import { CheckCircle, ImageSquare, Plus, Trash, UploadSimple, X } from "@phosphor-icons/react";

const emptyPreset = {
  id: null, name: "", school_name: "", tagline: "", footer: "",
  watermark: "", header_image_path: null,
};

export default function BrandingSettings() {
  const confirm = useConfirmation();
  const [presets, setPresets] = useState([]);
  const [draft, setDraft] = useState(emptyPreset);
  const [crestUrl, setCrestUrl] = useState("");
  const [saving, setSaving] = useState(false);
  const [uploading, setUploading] = useState(false);

  const load = async (preferId = null) => {
    const { data } = await api.get("/branding-presets");
    setPresets(data);
    const selected = data.find((item) => item.id === preferId)
      || data.find((item) => item.id === draft.id) || data[0] || emptyPreset;
    setDraft({ ...emptyPreset, ...selected });
  };

  useEffect(() => {
    load().catch(() => toast.error("Could not load branding presets"));
  }, []); // eslint-disable-line

  useEffect(() => {
    if (!draft.id || !draft.header_image_path) {
      setCrestUrl("");
      return undefined;
    }
    let active = true;
    let objectUrl = null;
    api.get(`/branding-presets/${draft.id}/image`, { responseType: "blob" })
      .then(({ data }) => {
        if (!active) return;
        objectUrl = URL.createObjectURL(data);
        setCrestUrl(objectUrl);
      })
      .catch(() => active && setCrestUrl(""));
    return () => {
      active = false;
      if (objectUrl) URL.revokeObjectURL(objectUrl);
    };
  }, [draft.id, draft.header_image_path]);

  const update = (key) => (event) =>
    setDraft((current) => ({ ...current, [key]: event.target.value }));

  const save = async (event) => {
    event.preventDefault();
    if (!draft.name.trim()) return toast.error("Give this branding preset a name");
    setSaving(true);
    try {
      const payload = {
        name: draft.name, school_name: draft.school_name, tagline: draft.tagline,
        footer: draft.footer, watermark: draft.watermark,
      };
      const { data } = draft.id
        ? await api.patch(`/branding-presets/${draft.id}`, payload)
        : await api.post("/branding-presets", payload);
      await load(data.id);
      toast.success(draft.id ? "Branding preset updated" : "Branding preset saved");
    } catch (err) {
      toast.error(err?.response?.data?.detail || "Could not save branding");
    } finally {
      setSaving(false);
    }
  };

  const upload = async (file) => {
    if (!file || !draft.id) return;
    const body = new FormData();
    body.append("file", file);
    setUploading(true);
    try {
      const { data } = await api.post(`/branding-presets/${draft.id}/image`, body);
      setDraft((current) => ({ ...current, ...data }));
      await load(draft.id);
      toast.success("School logo uploaded");
    } catch (err) {
      toast.error(err?.response?.data?.detail || "School logo upload failed");
    } finally {
      setUploading(false);
    }
  };

  const removeLogo = async () => {
    if (!draft.id) return;
    try {
      const { data } = await api.delete(`/branding-presets/${draft.id}/image`);
      setDraft((current) => ({ ...current, ...data }));
      setCrestUrl("");
      await load(draft.id);
      toast.success("Logo removed from this preset");
    } catch (err) {
      toast.error(err?.response?.data?.detail || "Could not remove logo");
    }
  };

  const removePreset = async () => {
    if (!draft.id || !(await confirm({
      title: "Delete branding preset?",
      description: `Delete “${draft.name}”? Existing papers keep their saved branding snapshot.`,
      confirmLabel: "Delete preset",
      tone: "danger",
    }))) return;
    try {
      await api.delete(`/branding-presets/${draft.id}`);
      setDraft(emptyPreset);
      await load();
      toast.success("Branding preset deleted");
    } catch (err) {
      toast.error(err?.response?.data?.detail || "Could not delete branding");
    }
  };

  return (
    <div className="min-h-screen bg-[#FAFAFA]">
      <Header />
      <main className="max-w-6xl mx-auto p-6 md:p-12">
        <div className="overline text-neutral-500 mb-2">// Settings / branding</div>
        <h1 className="font-display text-4xl md:text-5xl mb-3">Saved branding presets</h1>
        <p className="text-neutral-600 max-w-3xl mb-8">
          Save multiple school identities and choose one while creating or viewing a paper.
          Existing papers keep their branding snapshot when a preset changes or is deleted.
        </p>

        <div className="grid grid-cols-1 lg:grid-cols-4 gap-6">
          <aside className="qp-card">
            <button type="button" onClick={() => setDraft(emptyPreset)} className="qp-btn qp-btn-primary w-full mb-4" data-testid="new-branding-preset">
              <Plus size={16} weight="bold" /> New preset
            </button>
            <div className="space-y-2">
              {presets.map((preset) => (
                <button key={preset.id} type="button" onClick={() => setDraft({ ...emptyPreset, ...preset })}
                  className={`w-full border-2 p-3 text-left ${draft.id === preset.id ? "border-black bg-neutral-100" : "border-neutral-200 bg-white"}`}
                  data-testid={`branding-preset-${preset.id}`}>
                  <strong className="block">{preset.name}</strong>
                  <span className="text-xs text-neutral-500">{preset.school_name || "No school name"}</span>
                </button>
              ))}
              {!presets.length && <p className="text-sm text-neutral-500">No saved branding yet.</p>}
            </div>
          </aside>

          <form onSubmit={save} className="lg:col-span-3 space-y-6">
            <section className="qp-card">
              <div className="flex flex-wrap items-center justify-between gap-3 mb-5">
                <div className="overline text-neutral-500">// {draft.id ? "Edit preset" : "New preset"}</div>
                {draft.id && <button type="button" onClick={removePreset} className="qp-btn qp-btn-secondary text-xs" data-testid="delete-branding-preset"><Trash size={14} weight="bold" /> Delete preset</button>}
              </div>
              <label className="qp-label" htmlFor="branding-name">Preset name</label>
              <input id="branding-name" value={draft.name} onChange={update("name")} className="qp-input mb-5" placeholder="Main school branding" maxLength={100} data-testid="branding-name" />
              <label className="qp-label" htmlFor="settings-school-name">School name</label>
              <input id="settings-school-name" value={draft.school_name} onChange={update("school_name")} className="qp-input mb-5" placeholder="St. Xavier's Higher Secondary School" maxLength={200} />
              <label className="qp-label" htmlFor="settings-tagline">Tagline / address</label>
              <input id="settings-tagline" value={draft.tagline} onChange={update("tagline")} className="qp-input mb-5" placeholder="Estd. 1858 · Mumbai" maxLength={300} />
              <label className="qp-label" htmlFor="settings-footer">Footer stamp</label>
              <input id="settings-footer" value={draft.footer} onChange={update("footer")} className="qp-input mb-5" placeholder="Confidential - for enrolled students only" maxLength={300} />
              <label className="qp-label" htmlFor="settings-watermark">Watermark</label>
              <input id="settings-watermark" value={draft.watermark} onChange={update("watermark")} className="qp-input mb-6" placeholder="CONFIDENTIAL" maxLength={100} />
              <button type="submit" disabled={saving} className="qp-btn qp-btn-primary" data-testid="save-settings-branding"><CheckCircle size={16} weight="bold" /> {saving ? "Saving..." : "Save preset"}</button>
            </section>

            <section className="qp-card">
              <div className="overline text-neutral-500 mb-5">// School logo</div>
              <div className="branding-crest-preview">
                {crestUrl ? <img src={crestUrl} alt="School logo" /> : <><ImageSquare size={34} /><span>No logo uploaded</span></>}
              </div>
              <div className="flex flex-col gap-2 mt-4 sm:flex-row">
                <label className={`qp-btn qp-btn-secondary flex-1 cursor-pointer ${!draft.id ? "opacity-50 pointer-events-none" : ""}`}>
                  <UploadSimple size={16} weight="bold" /> {uploading ? "Uploading..." : "Upload logo"}
                  <input type="file" accept="image/jpeg,image/png,.jpg,.jpeg,.png" onChange={(event) => upload(event.target.files?.[0])} className="sr-only" disabled={uploading || !draft.id} />
                </label>
                {draft.header_image_path && <button type="button" onClick={removeLogo} className="qp-btn qp-btn-secondary" data-testid="remove-branding-logo"><X size={16} weight="bold" /> Remove</button>}
              </div>
              {!draft.id && <p className="text-xs text-neutral-500 mt-3">Save the preset before uploading its logo.</p>}
            </section>

            <section className="qp-card">
              <div className="overline text-neutral-500 mb-5">// Preview on paper</div>
              <div className="branding-paper-preview">
                <div className="branding-paper-header">
                  {crestUrl && <img src={crestUrl} alt="" />}
                  <div><strong>{draft.school_name || "Your school name"}</strong>{draft.tagline && <em>{draft.tagline}</em>}</div>
                </div>
                <div className="branding-paper-title">Physics · Class 10 · Mock</div>
                <div className="branding-paper-meta">Class: 10&nbsp;&nbsp; Subject: Physics&nbsp;&nbsp; Marks: 80</div>
                {draft.footer && <div className="branding-paper-footer">{draft.footer}<span>Page 1</span></div>}
                {draft.watermark && <div className="branding-paper-watermark">{draft.watermark}</div>}
              </div>
            </section>
          </form>
        </div>
      </main>
    </div>
  );
}
