import React, { useEffect, useState } from "react";
import { updateAllocation } from "@/lib/sectionBlueprint";

const labels = { easy: "Simple", medium: "Moderate", hard: "Challenging" };

export default function SectionBlueprintEditor({ blueprint, bookIds, details, allocations, onChange, difficulty, extractingId, onExtract }) {
  const [active, setActive] = useState(blueprint?.sections?.[0]?.id || "");
  const [search, setSearch] = useState("");
  useEffect(() => { setActive(current => blueprint?.sections?.some(s => s.id === current) ? current : blueprint?.sections?.[0]?.id || ""); }, [blueprint]);
  const section = blueprint?.sections?.find(s => s.id === active);
  const topics = bookIds.flatMap(id => details[id]?.topic_catalog || []);
  return <section className="qp-card section-blueprint" data-testid="section-blueprint-editor">
    <h2 className="font-bold uppercase tracking-wide text-sm">Section-wise topic breakdown &amp; blueprint</h2>
    <p className="text-xs text-neutral-500 mt-2 mb-4">Map textbook topics to each exam section. Counts refer to numbered questions; compound subparts stay together.</p>
    {!blueprint && <p className="text-sm mb-4">Choose a Paper Blueprint preset or analyze your custom blueprint to define sections.</p>}
    <div className="space-y-2 mb-4">
      {!bookIds.length && <p className="text-sm">Select a textbook to load its topics.</p>}
      {bookIds.map(id => <div key={id} className="flex flex-wrap items-center justify-between gap-2 text-xs border-b pb-2">
        <span>{details[id]?.original_filename || "Loading textbook…"}</span>
        <button type="button" className="qp-btn qp-btn-secondary text-xs" disabled={Boolean(extractingId)} onClick={() => onExtract(id)}>
          {extractingId === id ? `Extracting ${details[id]?.topic_extraction?.completed || 0}/${details[id]?.topic_extraction?.total || "…"}` : details[id]?.topic_catalog?.length ? "Refresh topics" : "Extract topics"}
        </button>
        {details[id]?.topic_extraction?.status === "failed" && <p role="alert" className="w-full text-red-700">{details[id].topic_extraction.error}</p>}
      </div>)}
    </div>
    {blueprint?.errors?.length > 0 && <div role="alert" className="text-sm text-red-700 mb-4">{blueprint.errors.join(" ")}</div>}
    {blueprint && <>
      <div className="section-tabs" role="tablist" aria-label="Blueprint sections">
        {blueprint.sections.map(s => {
          const count = s.groups.reduce((n, g) => n + g.count, 0);
          const assigned = allocations.filter(a => a.section_id === s.id).reduce((n, a) => n + a.count, 0);
          return <button type="button" role="tab" aria-selected={active === s.id} aria-controls={`section-panel-${s.id}`} id={`section-tab-${s.id}`} key={s.id}
            className={active === s.id ? "is-active" : ""} onClick={() => setActive(s.id)}>
            <strong>{s.title}</strong><small>{assigned}/{count} Qs · {s.groups.reduce((n, g) => n + g.count * g.marks, 0)} offered marks</small>
          </button>;
        })}
      </div>
      {section && <div role="tabpanel" id={`section-panel-${section.id}`} aria-labelledby={`section-tab-${section.id}`}>
        {section.attempt_count && <p className="text-xs my-3">Attempt {section.attempt_count} of the offered questions.</p>}
        {section.choice_set && <p className="text-xs my-3">Alternative section: choose one section in {section.choice_set}.</p>}
        <input aria-label="Search textbook topics" className="qp-input my-3" placeholder="Search chapters or topics…" value={search} onChange={e => setSearch(e.target.value)} />
        {section.groups.map(group => {
          const assigned = allocations.filter(a => a.section_id === section.id && a.group_id === group.id).reduce((n, a) => n + a.count, 0);
          return <fieldset key={group.id} className="section-topic-group">
            <legend>{group.label || group.format} · {group.marks} marks each · <span className={assigned !== group.count ? "text-amber-700" : "text-green-700"}>{assigned}/{group.count} assigned</span></legend>
            {topics.filter(t => `${t.name} ${t.parent_topic} ${t.book_label}`.toLowerCase().includes(search.toLowerCase())).map(topic => {
              const row = allocations.find(a => a.section_id === section.id && a.group_id === group.id && a.textbook_id === topic.textbook_id && a.topic_id === topic.topic_id);
              const change = patch => onChange(updateAllocation(allocations, section.id, group.id, topic, patch));
              return <div className={`section-topic-row ${row ? "is-selected" : ""}`} key={`${topic.textbook_id}:${topic.topic_id}`}>
                <label className="section-topic-title"><input type="checkbox" checked={Boolean(row)} onChange={e => change(e.target.checked ? {} : null)} /><span><strong>{topic.name}</strong><small>{topic.parent_topic && `${topic.parent_topic} · `}{topic.book_label}</small></span></label>
                {row && <div className="section-topic-controls">
                  <label>Difficulty<select aria-label={`Difficulty for ${topic.name} in ${section.id}`} value={row.difficulty || ""} onChange={e => change({ difficulty: e.target.value || null })}>
                    <option value="">Default ({labels[difficulty]})</option>{Object.entries(labels).map(([key, label]) => <option key={key} value={key}>{label}</option>)}
                  </select></label>
                  <label className="diagram-toggle"><input type="checkbox" checked={row.diagrams} onChange={e => change({ diagrams: e.target.checked })} />At least one diagram</label>
                  <label>Questions<input aria-label={`Question count for ${topic.name} in ${section.id}`} type="number" min="1" max={group.count} value={row.count} onChange={e => change({ count: Number(e.target.value) })} /><small>{row.count * group.marks} marks</small></label>
                </div>}
              </div>;
            })}
            {!topics.length && <p className="text-sm p-3 text-neutral-500">No topics loaded. Extract topics from a selected textbook above.</p>}
          </fieldset>;
        })}
      </div>}
    </>}
  </section>;
}
