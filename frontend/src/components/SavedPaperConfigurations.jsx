import React, { useEffect, useState } from "react";
import { Check, FloppyDisk, PencilSimple, Trash, X } from "@phosphor-icons/react";
import { api } from "@/lib/api";
import { toast } from "sonner";

const describe = (config) =>
  `${config.total_marks} marks · ${config.duration_minutes} min · ${config.difficulty}`;

export default function SavedPaperConfigurations({ currentConfiguration, onApply }) {
  const [configurations, setConfigurations] = useState([]);
  const [loading, setLoading] = useState(true);
  const [saving, setSaving] = useState(false);
  const [selected, setSelected] = useState(null);
  const [name, setName] = useState("");
  const [editingId, setEditingId] = useState(null);

  const load = async () => {
    try {
      const { data } = await api.get("/paper-configurations");
      setConfigurations(data);
    } catch (error) {
      toast.error(error?.response?.data?.detail || "Could not load saved configurations");
    } finally {
      setLoading(false);
    }
  };

  useEffect(() => { load(); }, []);

  const save = async () => {
    const trimmed = name.trim();
    if (!trimmed) return toast.error("Configuration name required");
    setSaving(true);
    try {
      const { data } = editingId
        ? await api.patch(`/paper-configurations/${editingId}`, { name: trimmed, ...currentConfiguration })
        : await api.post("/paper-configurations", { name: trimmed, ...currentConfiguration });
      setConfigurations((current) => editingId
        ? current.map((item) => item.id === data.id ? data : item)
        : [data, ...current]);
      setName("");
      setEditingId(null);
      toast.success(editingId ? "Configuration updated" : "Configuration saved");
    } catch (error) {
      toast.error(error?.response?.data?.detail || "Could not save configuration");
    } finally {
      setSaving(false);
    }
  };

  const remove = async (config) => {
    if (!window.confirm(`Delete "${config.name}"?`)) return;
    try {
      await api.delete(`/paper-configurations/${config.id}`);
      setConfigurations((current) => current.filter((item) => item.id !== config.id));
      if (selected?.id === config.id) setSelected(null);
      toast.success("Configuration deleted");
    } catch (error) {
      toast.error(error?.response?.data?.detail || "Could not delete configuration");
    }
  };

  const beginEdit = (config) => {
    setEditingId(config.id);
    setName(config.name);
  };

  return (
    <section className="qp-card mb-6" data-testid="saved-paper-configurations">
      <div className="flex flex-wrap items-start justify-between gap-4">
        <div>
          <div className="overline text-neutral-500">// SAVED CONFIGURATIONS</div>
          <h2 className="font-bold uppercase tracking-wide text-sm mt-2">Reuse your setup</h2>
          <p className="text-xs text-neutral-500 mt-2 max-w-2xl">
            Save a reusable paper template, including its textbooks, topics, allocations, and generation settings. Paper title and branding remain specific to each paper.
          </p>
        </div>
        <div className="flex w-full sm:w-auto gap-2">
          <input
            className="qp-input min-w-0"
            value={name}
            onChange={(event) => setName(event.target.value)}
            placeholder="Configuration name"
            maxLength={100}
            data-testid="saved-configuration-name"
          />
          <button type="button" className="qp-btn qp-btn-secondary shrink-0" onClick={save} disabled={saving}>
            <FloppyDisk size={16} /> {editingId ? "Update" : "Save"}
          </button>
          {editingId && <button type="button" className="qp-btn qp-btn-secondary shrink-0" onClick={() => { setEditingId(null); setName(""); }} aria-label="Cancel editing"><X size={16} /></button>}
        </div>
      </div>

      {loading && <p className="text-xs text-neutral-500 mt-5">Loading saved configurations…</p>}
      {!loading && configurations.length === 0 && <p className="text-xs text-neutral-500 mt-5">No saved configurations yet.</p>}
      {configurations.length > 0 && (
        <div className="mt-5 grid gap-2">
          {configurations.map((config) => (
            <div key={config.id} className={`border-2 p-3 ${selected?.id === config.id ? "border-[#002FA7] bg-blue-50" : "border-neutral-200 bg-white"}`}>
              <div className="flex flex-wrap items-center justify-between gap-3">
                <button type="button" className="min-w-0 text-left" onClick={() => setSelected(config)} data-testid={`saved-configuration-${config.id}`}>
                  <strong className="block truncate text-sm">{config.name}</strong>
                  <span className="text-xs text-neutral-500">{describe(config)}</span>
                </button>
                <div className="flex gap-1">
                  <button type="button" className="p-2 hover:bg-neutral-100" onClick={() => beginEdit(config)} aria-label={`Edit ${config.name}`}><PencilSimple size={16} /></button>
                  <button type="button" className="p-2 hover:bg-red-50 hover:text-red-700" onClick={() => remove(config)} aria-label={`Delete ${config.name}`}><Trash size={16} /></button>
                </div>
              </div>
              {selected?.id === config.id && (
                <div className="mt-3 border-t border-neutral-200 pt-3">
                  <p className="text-xs text-neutral-600">
                    Applying this template restores its sources and generation settings. Unavailable textbooks or topics will be flagged for review; title and branding stay unchanged.
                  </p>
                  <button type="button" className="qp-btn qp-btn-primary mt-3" onClick={() => { onApply(config); setSelected(null); }}>
                    <Check size={16} weight="bold" /> Confirm and apply
                  </button>
                </div>
              )}
            </div>
          ))}
        </div>
      )}
    </section>
  );
}
