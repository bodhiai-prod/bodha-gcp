import React, { useEffect, useMemo, useState } from "react";
import { CaretDown } from "@phosphor-icons/react";
import { groupSlots, updateAllocation } from "@/lib/sectionBlueprint";

const labels = { easy: "Simple", medium: "Moderate", hard: "Challenging" };

function topicUnits(detail) {
  const rows = detail?.topic_catalog || [];
  return rows.filter(row => !row.parent_topic).map(parent => ({
    ...parent, subtopics: rows.filter(row => row.parent_topic === parent.name),
  })).filter(unit => unit.name);
}

export default function SectionBlueprintEditor({ blueprint, bookIds, details, allocations, onChange, difficulty, onExtract }) {
  const [active, setActive] = useState(blueprint?.sections?.[0]?.id || "");
  const [activeBook, setActiveBook] = useState(bookIds[0] || "");
  const [search, setSearch] = useState("");
  const [expandedUnits, setExpandedUnits] = useState({});
  useEffect(() => setActive(current => blueprint?.sections?.some(s => s.id === current) ? current : blueprint?.sections?.[0]?.id || ""), [blueprint]);
  useEffect(() => setActiveBook(current => bookIds.includes(current) ? current : bookIds[0] || ""), [bookIds]);

  const sectionIndex = blueprint?.sections?.findIndex(s => s.id === active) ?? -1;
  const section = blueprint?.sections?.[sectionIndex];
  const units = useMemo(() => topicUnits(details[activeBook]), [activeBook, details]);
  const matchesSearch = topic => `${topic.name} ${topic.parent_topic || ""}`.toLowerCase().includes(search.toLowerCase());
  const visibleUnits = units.map(unit => ({ ...unit, subtopics: unit.subtopics.filter(matchesSearch) }))
    .filter(unit => matchesSearch(unit) || unit.subtopics.length);

  const topicRow = (topic, group, assigned, { parent, subtopics = [], slot = null } = {}) => {
    const inTarget = a => a.section_id === section.id && a.group_id === group.id && (slot ? a.slot_id === slot.id : !a.slot_id);
    const row = allocations.find(a => inTarget(a) && a.textbook_id === topic.textbook_id && a.topic_id === topic.topic_id);
    const parentRow = parent && allocations.find(a => inTarget(a) && a.textbook_id === parent.textbook_id && a.topic_id === parent.topic_id);
    const inheritedCount = subtopics.reduce((total, subtopic) => total + (allocations.find(a => inTarget(a) && a.textbook_id === subtopic.textbook_id && a.topic_id === subtopic.topic_id)?.count || 0), 0);
    const inheritsSubtopics = !topic.parent_topic && inheritedCount > 0;
    const baseAllocations = parentRow && !row ? allocations.filter(a => a !== parentRow) : allocations;
    const baseAssigned = assigned - (parentRow && !row ? parentRow.count : 0);
    const remaining = Math.max(0, (slot ? 1 : group.count) - baseAssigned);
    const selectionLocked = !row && remaining === 0;
    const change = patch => {
      if (inheritsSubtopics && patch === null) {
        const childIds = new Set(subtopics.map(subtopic => subtopic.topic_id));
        onChange(allocations.filter(allocation => {
          const isThisChapter = inTarget(allocation) && allocation.textbook_id === topic.textbook_id && (allocation.topic_id === topic.topic_id || childIds.has(allocation.topic_id));
          return !isThisChapter;
        }));
        return;
      }
      if (patch !== null && selectionLocked) return;
      const nextPatch = patch && Object.hasOwn(patch, "count")
        ? { ...patch, count: Math.min(Math.max(1, Number(patch.count) || 1), (row?.count || 0) + remaining) }
        : patch;
      onChange(updateAllocation(baseAllocations, section.id, group.id, slot?.id || null, topic, nextPatch));
    };
    return <div className={`section-topic-row ${row || inheritsSubtopics ? "is-selected" : ""}`} key={topic.topic_id}>
      <label className="section-topic-title"><input type="checkbox" checked={Boolean(row) || inheritsSubtopics} disabled={selectionLocked} onChange={e => change(e.target.checked ? {} : null)} /><span><strong>{topic.name}</strong>{topic.parent_topic && <small>{topic.parent_topic}</small>}</span></label>
      {inheritsSubtopics && <div className="section-topic-inherited-count">{inheritedCount} question{inheritedCount === 1 ? "" : "s"} inherited from selected subtopics</div>}
      {row && !inheritsSubtopics && <div className="section-topic-controls">
        <label>Difficulty<select aria-label={`Difficulty for ${topic.name} in ${section.id}`} value={row.difficulty || ""} onChange={e => change({ difficulty: e.target.value || null })}><option value="">Default ({labels[difficulty]})</option>{Object.entries(labels).map(([key, label]) => <option key={key} value={key}>{label}</option>)}</select></label>
        <label className="diagram-toggle"><input type="checkbox" checked={row.diagrams} onChange={e => change({ diagrams: e.target.checked })} />At least one diagram</label>
        <div className="question-stepper-field">
          <span>Questions</span>
          <div className="question-stepper" role="group" aria-label={`Question count for ${topic.name} in ${section.id}`}>
            <button
              type="button"
              aria-label={`Decrease questions for ${topic.name}`}
              disabled={row.count <= 1}
              onClick={() => change({ count: row.count - 1 })}
            >
              −
            </button>
            <output aria-live="polite">{row.count}</output>
            <button
              type="button"
              aria-label={`Increase questions for ${topic.name}`}
              disabled={remaining <= 0}
              onClick={() => change({ count: row.count + 1 })}
            >
              +
            </button>
          </div>
          <small>{row.count * (slot?.marks || group.marks)} marks</small>
        </div>
      </div>}
    </div>;
  };

  return <section className="qp-card section-blueprint" data-testid="section-blueprint-editor">
    <h2 className="font-bold uppercase tracking-wide text-sm">Section-wise topic breakdown &amp; blueprint</h2>
    <p className="text-xs text-neutral-500 mt-2 mb-4">Choose a book, then map its units and subtopics to each printable question part. Compound questions retain their official numbering when the paper is generated.</p>
    {!blueprint && <p className="text-sm mb-4">Choose a Paper Blueprint preset or analyze your custom blueprint to define sections.</p>}
    <div className="space-y-2 mb-4">
      {!bookIds.length && <p className="text-sm">Select a textbook to load its topics.</p>}
      {bookIds.map(id => { const extraction = details[id]?.topic_extraction; const active = ["queued", "running"].includes(extraction?.status); return <div key={id} className="flex flex-wrap items-center justify-between gap-2 text-xs border-b pb-2"><span>{details[id]?.original_filename || "Loading textbook…"}</span><button type="button" className="qp-btn qp-btn-secondary text-xs" disabled={active} onClick={() => onExtract(id)}>{extraction?.status === "queued" ? "Queued" : extraction?.status === "running" ? `Extracting ${extraction.completed || 0}/${extraction.total || "…"}` : details[id]?.topic_catalog?.length ? "Refresh topics" : "Extract topics"}</button>{extraction?.status === "failed" && <p role="alert" className="w-full text-red-700">{extraction.error}</p>}</div>; })}
    </div>
    {blueprint?.errors?.length > 0 && <div role="alert" className="text-sm text-red-700 mb-4">{blueprint.errors.join(" ")}</div>}
    {blueprint && <>
      {bookIds.length > 0 && <div className="section-tabs blueprint-book-tabs mb-4" role="tablist" aria-label="Textbooks">{bookIds.map(id => <button type="button" role="tab" key={id} aria-selected={activeBook === id} className={activeBook === id ? "is-active" : ""} onClick={() => { setActiveBook(id); setSearch(""); }} data-testid={`blueprint-book-${id}`}><strong>{details[id]?.original_filename || "Loading textbook…"}</strong><small>{topicUnits(details[id]).length} units / modules</small></button>)}</div>}
      <div className="section-tabs" role="tablist" aria-label="Blueprint sections">{blueprint.sections.map(s => { const slots = s.groups.flatMap(groupSlots); const count = slots.length; const assigned = allocations.filter(a => a.section_id === s.id).reduce((n, a) => n + a.count, 0); const offered = slots.reduce((n, slot) => n + slot.marks, 0); const attempted = s.attempt_count ? `${s.attempt_count} questions attempted` : s.choice_set ? "alternative stream" : `${offered} marks`; return <button type="button" role="tab" aria-selected={active === s.id} aria-controls={`section-panel-${s.id}`} id={`section-tab-${s.id}`} key={s.id} className={active === s.id ? "is-active" : ""} onClick={() => setActive(s.id)}><strong>{s.title}</strong><small>{assigned}/{count} parts · {offered} offered marks · {attempted}</small></button>; })}</div>
      {section && <div role="tabpanel" id={`section-panel-${section.id}`} aria-labelledby={`section-tab-${section.id}`}>
        <div className="section-navigation"><button type="button" className="qp-btn qp-btn-secondary text-xs" disabled={sectionIndex <= 0} onClick={() => setActive(blueprint.sections[sectionIndex - 1].id)} data-testid="blueprint-previous-section">Previous section</button><span className="text-xs font-bold">Section {sectionIndex + 1} of {blueprint.sections.length}</span><button type="button" className="qp-btn qp-btn-secondary text-xs" disabled={sectionIndex >= blueprint.sections.length - 1} onClick={() => setActive(blueprint.sections[sectionIndex + 1].id)} data-testid="blueprint-next-section">Next section</button></div>
        {section.attempt_count && <p className="text-xs my-3">Attempt {section.attempt_count} of the offered questions.</p>}{section.choice_set && <p className="text-xs my-3">Alternative section: choose one section in {section.choice_set}.</p>}
        <input aria-label="Search selected textbook topics" className="qp-input my-3" placeholder="Search units or subtopics…" value={search} onChange={e => setSearch(e.target.value)} />
        {section.groups.flatMap(group => (group.slots?.length ? groupSlots(group) : [null]).map(slot => {
          const assigned = allocations.filter(a => a.section_id === section.id && a.group_id === group.id && (slot ? a.slot_id === slot.id : !a.slot_id)).reduce((n, a) => n + a.count, 0);
          const target = slot ? 1 : group.count;
          const isComplete = assigned >= target;
          const heading = slot ? ([slot.parent_label, slot.part_label].filter(Boolean).join(" ") || group.label || group.format) : (group.label || group.format);
          const marks = slot?.marks || group.marks;
          return <fieldset key={`${group.id}-${slot?.id || "legacy"}`} className={`section-topic-group ${isComplete ? "is-complete" : ""}`}><legend>{heading} · {marks} mark{marks === 1 ? "" : "s"} {slot ? "" : "each"} · <span className="section-assignment-counter" aria-live="polite"><strong>{assigned}</strong><span>/{target} assigned</span></span></legend>{slot?.lead_in && <p className="text-xs italic my-2">{slot.lead_in}</p>}{isComplete && <p className="section-assignment-limit" role="status">Assignment target reached. Deselect the current topic to assign another.</p>}<div className="chapter-selection-scroll blueprint-chapter-scroll" aria-label={`Chapters and subtopics for ${heading}`} data-testid={`blueprint-topic-scroll-${group.id}-${slot?.id || "legacy"}`}>{visibleUnits.map(unit => { const unitKey = `${activeBook}:${unit.topic_id}`; const expanded = expandedUnits[unitKey] || Boolean(search); return <div className="blueprint-unit" key={unit.topic_id}>{topicRow(unit, group, assigned, { subtopics: unit.subtopics, slot })}{unit.subtopics.length > 0 && <><button type="button" className="blueprint-unit-toggle" aria-expanded={expanded} onClick={() => setExpandedUnits(curr => ({ ...curr, [unitKey]: !expanded }))}><span>{expanded ? "Show less" : "Expand more"} · {unit.subtopics.length} subtopics</span><CaretDown size={16} className={expanded ? "rotate-180" : ""} /></button>{expanded && <div className="blueprint-subtopics">{unit.subtopics.map(topic => topicRow(topic, group, assigned, { parent: unit, slot }))}</div>}</>}</div>; })}{!visibleUnits.length && <p className="text-sm p-3 text-neutral-500">No matching units or subtopics. Extract topics from the selected textbook above.</p>}</div></fieldset>;
        }))}
      </div>}
    </>}
  </section>;
}
