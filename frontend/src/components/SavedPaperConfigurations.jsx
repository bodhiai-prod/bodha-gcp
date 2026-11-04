import React, { useEffect, useState } from "react";
import { CaretDown, Check, FloppyDisk, PencilSimple, Trash, X } from "@phosphor-icons/react";
import { api } from "@/lib/api";
import { toast } from "sonner";
import { useConfirmation } from "@/context/ConfirmationContext";

const describe = (config) =>
  `${config.total_marks} marks · ${config.duration_minutes} min · ${config.difficulty}`;

export default function SavedPaperConfigurations({ currentConfiguration, onApply }) {
  const confirm = useConfirmation();
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
    if (!(await confirm({
      title: "Delete saved configuration?",
      description: `Delete "${config.name}"?`,
      confirmLabel: "Delete configuration",
      tone: "danger",
    }))) return;
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
      <div className="saved-config-row">
        <div>
          <h2 className="font-display text-2xl">Saved configurations</h2>
          <p className="text-sm text-neutral-600 mt-1">Reuse your setup</p>
        </div>
        <div className="relative min-w-0">
          <label className="sr-only" htmlFor="saved-configuration-select">Choose a saved configuration</label>
          <select
            id="saved-configuration-select"
            className="qp-input w-full appearance-none pr-10"
            value={selected?.id || ""}
            onChange={(event) => setSelected(configurations.find((config) => config.id === event.target.value) || null)}
            disabled={loading || configurations.length === 0}
            data-testid="saved-configuration-select"
          >
            <option value="">{loading ? "Loading configurations…" : configurations.length ? "Choose a saved configuration" : "No saved configurations yet"}</option>
            {configurations.map((config) => (
              <option key={config.id} value={config.id}>{config.name} — {describe(config)}</option>
            ))}
          </select>
          <CaretDown className="pointer-events-none absolute right-3 top-1/2 -translate-y-1/2" size={16} aria-hidden="true" />
        </div>
        <div className="flex min-w-0 gap-2">
          <input
            className="qp-input min-w-0"
            aria-label="Configuration name"
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

      {selected && (
        <div className="mt-4 border-2 border-[#002FA7] bg-blue-50 p-3">
          <div className="flex flex-wrap items-center justify-between gap-3">
            <div className="min-w-0">
              <strong className="block truncate text-sm">{selected.name}</strong>
              <span className="text-xs text-neutral-500">{describe(selected)}</span>
            </div>
            <div className="flex gap-1">
              <button type="button" className="p-2 hover:bg-neutral-100" onClick={() => beginEdit(selected)} aria-label={`Edit ${selected.name}`}><PencilSimple size={16} /></button>
              <button type="button" className="p-2 hover:bg-red-50 hover:text-red-700" onClick={() => remove(selected)} aria-label={`Delete ${selected.name}`}><Trash size={16} /></button>
            </div>
          </div>
          <div className="mt-3 border-t border-neutral-200 pt-3">
            <p className="text-xs text-neutral-600">
              Applying this template restores its sources and generation settings. Unavailable textbooks or topics will be flagged for review; title and branding stay unchanged.
            </p>
            <button type="button" className="qp-btn qp-btn-primary mt-3" onClick={() => { onApply(selected); setSelected(null); }}>
              <Check size={16} weight="bold" /> Confirm and apply
            </button>
          </div>
        </div>
      )}
    </section>
  );
}
