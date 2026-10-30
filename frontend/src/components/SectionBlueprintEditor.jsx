import React, { useEffect, useMemo, useState } from "react";
import { CaretDown } from "@phosphor-icons/react";
import { allocationBuckets, groupSlots, normalizeBucketAllocations, updateAllocation } from "@/lib/sectionBlueprint";
import QuestionCountStepper from "@/components/QuestionCountStepper";
import { countDiagramSelections, MAX_DIAGRAMS_PER_PAPER } from "@/lib/diagramSelection";
import { catalogTopics, generateBlueprintTopicAllocations } from "@/lib/autoTopicAllocation";

const labels = { easy: "Simple", medium: "Moderate", hard: "Challenging" };
const formatLabels = { mcq: "Multiple choice", short_answer: "Short answer", long_answer: "Long answer", fill_blank: "Fill in the blank", true_false: "True / false" };

function topicUnits(detail) {
  const rows = detail?.topic_catalog || [];
  return rows.filter(row => !row.parent_topic).map(parent => ({
    ...parent, subtopics: rows.filter(row => row.parent_topic === parent.name),
  })).filter(unit => unit.name);
}

function bucketHeading(group, bucket) {
  const first = bucket.slots[0];
  const last = bucket.slots.at(-1);
  const number = value => String(value?.parent_label || "").match(/Question\s+(\d+)/i)?.[1];
  const firstNumber = number(first), lastNumber = number(last);
  const range = firstNumber && lastNumber
    ? firstNumber === lastNumber ? `Question ${firstNumber}` : `Questions ${firstNumber}–${lastNumber}`
    : group.label || formatLabels[bucket.format] || bucket.format;
  return `${range} · ${formatLabels[bucket.format] || bucket.format} · ${bucket.marks} mark${bucket.marks === 1 ? "" : "s"} each`;
}

export default function SectionBlueprintEditor({ blueprint, bookIds, details, allocations, onChange, difficulty, onExtract }) {
  const [active, setActive] = useState(blueprint?.sections?.[0]?.id || "");
  const [activeBook, setActiveBook] = useState(bookIds[0] || "");
  const [search, setSearch] = useState("");
  // The topic picker behaves as an accordion: keeping a single key prevents
  // several long subtopic lists from competing for space at once.
  const [expandedUnit, setExpandedUnit] = useState(() => new Set());
  useEffect(() => setActive(current => blueprint?.sections?.some(s => s.id === current) ? current : blueprint?.sections?.[0]?.id || ""), [blueprint]);
  useEffect(() => setActiveBook(current => bookIds.includes(current) ? current : bookIds[0] || ""), [bookIds]);
  useEffect(() => {
    if (!blueprint) return;
    const normalized = normalizeBucketAllocations(allocations, blueprint);
    if (JSON.stringify(normalized) !== JSON.stringify(allocations)) onChange(normalized);
  }, [blueprint, allocations, onChange]);

  const sectionIndex = blueprint?.sections?.findIndex(s => s.id === active) ?? -1;
  const section = blueprint?.sections?.[sectionIndex];
  const units = useMemo(() => topicUnits(details[activeBook]), [activeBook, details]);
  const matchesSearch = topic => `${topic.name} ${topic.parent_topic || ""}`.toLowerCase().includes(search.toLowerCase());
  const visibleUnits = units.map(unit => ({ ...unit, subtopics: unit.subtopics.filter(matchesSearch) }))
    .filter(unit => matchesSearch(unit) || unit.subtopics.length);
  const selectedDiagrams = countDiagramSelections(allocations);
  const diagramLimitReached = selectedDiagrams >= MAX_DIAGRAMS_PER_PAPER;
  const canAutoAssign = Boolean(blueprint) && catalogTopics(bookIds, details).length > 0;

  const expandSelectedUnits = (rows) => {
    const keys = rows.flatMap((row) => {
      const catalog = details[row.textbook_id]?.topic_catalog || [];
      const topic = catalog.find((item) => item.topic_id === row.topic_id);
      const unit = topic?.parent_topic
        ? catalog.find((item) => !item.parent_topic && item.name === topic.parent_topic)
        : topic;
      return unit ? [`${row.section_id}:${row.group_id}:${row.bucket_id}:${row.textbook_id}:${unit.topic_id}`] : [];
    });
    if (keys.length) setExpandedUnit((current) => new Set([...current, ...keys]));
  };

  const autoAssign = () => {
    const next = generateBlueprintTopicAllocations(blueprint, bookIds, details);
    onChange(next);
    expandSelectedUnits(next);
  };

  const topicRow = (topic, group, bucket, assigned, { parent, subtopics = [] } = {}) => {
    const inTarget = allocation => allocation.section_id === section.id && allocation.group_id === group.id && allocation.bucket_id === bucket.id;
    const row = allocations.find(allocation => inTarget(allocation) && allocation.textbook_id === topic.textbook_id && allocation.topic_id === topic.topic_id);
    const parentRow = parent && allocations.find(allocation => inTarget(allocation) && allocation.textbook_id === parent.textbook_id && allocation.topic_id === parent.topic_id);
    const inheritedCount = subtopics.reduce((total, subtopic) => total + (allocations.find(allocation => inTarget(allocation) && allocation.textbook_id === subtopic.textbook_id && allocation.topic_id === subtopic.topic_id)?.count || 0), 0);
    const inheritsSubtopics = !topic.parent_topic && inheritedCount > 0;
    const baseAllocations = parentRow && !row ? allocations.filter(allocation => allocation !== parentRow) : allocations;
    const baseAssigned = assigned - (parentRow && !row ? parentRow.count : 0);
    const remaining = Math.max(0, bucket.slots.length - baseAssigned);
    const selectionLocked = !row && remaining === 0;
    const change = patch => {
      if (inheritsSubtopics && patch === null) {
        const childIds = new Set(subtopics.map(subtopic => subtopic.topic_id));
        onChange(allocations.filter(allocation => !(inTarget(allocation) && allocation.textbook_id === topic.textbook_id && (allocation.topic_id === topic.topic_id || childIds.has(allocation.topic_id)))));
        return;
      }
      if (patch !== null && selectionLocked) return;
      const nextPatch = patch && Object.hasOwn(patch, "count")
        ? { ...patch, count: Math.min(Math.max(1, Number(patch.count) || 1), (row?.count || 0) + remaining) }
        : patch;
      onChange(updateAllocation(baseAllocations, section.id, group.id, { bucketId: bucket.id }, topic, nextPatch));
    };
    return <div className={`section-topic-row ${row || inheritsSubtopics ? "is-selected" : ""}`} key={topic.topic_id}>
      <label className="section-topic-title"><input type="checkbox" checked={Boolean(row) || inheritsSubtopics} disabled={selectionLocked} onChange={event => change(event.target.checked ? {} : null)} /><span><strong>{topic.name}</strong>{topic.parent_topic && <small>{topic.parent_topic}</small>}</span></label>
      {inheritsSubtopics && <div className="section-topic-inherited-count">{inheritedCount} question{inheritedCount === 1 ? "" : "s"} assigned to selected subtopics</div>}
      {row && !inheritsSubtopics && <div className="section-topic-controls">
        <label>Difficulty<select aria-label={`Difficulty for ${topic.name} in ${bucket.id}`} value={row.difficulty || ""} onChange={event => change({ difficulty: event.target.value || null })}><option value="">Default ({labels[difficulty]})</option>{Object.entries(labels).map(([key, label]) => <option key={key} value={key}>{label}</option>)}</select></label>
        <label className="diagram-toggle"><input type="checkbox" checked={row.diagrams} disabled={!row.diagrams && diagramLimitReached} onChange={event => {
          if (event.target.checked && diagramLimitReached) return;
          change({ diagrams: event.target.checked });
        }} />At least one diagram</label>
        <div className="question-stepper-field"><span>Questions</span><QuestionCountStepper value={row.count} max={row.count + remaining} onChange={(count) => change({ count })} label={`questions for ${topic.name} in ${bucket.id}`} /><small>{row.count * bucket.marks} marks</small></div>
      </div>}
    </div>;
  };

  return <section className="qp-card section-blueprint" data-testid="section-blueprint-editor">
    <div className="flex flex-wrap items-center justify-between gap-2"><h2 className="font-bold uppercase tracking-wide text-sm">Section-wise topic breakdown &amp; blueprint</h2>{blueprint && <button type="button" className="qp-btn qp-btn-secondary text-xs" disabled={!canAutoAssign} onClick={autoAssign} data-testid="blueprint-auto-assign">Auto assign</button>}</div>
    <p className="text-xs text-neutral-500 mt-2 mb-4">Choose topics once for each question type and mark range. The generator keeps the blueprint’s individual question numbering and format rules.</p>
    {blueprint && !canAutoAssign && bookIds.length > 0 && <p className="text-xs text-neutral-500 mb-3">Extract topics from a selected textbook before auto assigning.</p>}
    <p className={`diagram-selection-status ${diagramLimitReached ? "is-limit" : ""}`} role={diagramLimitReached ? "alert" : "status"}>
      {selectedDiagrams} diagram{selectedDiagrams === 1 ? "" : "s"} selected{diagramLimitReached ? " — maximum reached" : ""}
    </p>
    {!blueprint && <p className="text-sm mb-4">Choose a Paper Blueprint preset or analyze your custom blueprint to define sections.</p>}
    <div className="space-y-2 mb-4">
      {!bookIds.length && <p className="text-sm">Select a textbook to load its topics.</p>}
      {bookIds.map(id => { const extraction = details[id]?.topic_extraction; const isExtracting = ["queued", "running"].includes(extraction?.status); return <div key={id} className="flex flex-wrap items-center justify-between gap-2 text-xs border-b pb-2"><span>{details[id]?.original_filename || "Loading textbook…"}</span><button type="button" className="qp-btn qp-btn-secondary text-xs" disabled={isExtracting} onClick={() => onExtract(id)}>{extraction?.status === "queued" ? "Queued" : extraction?.status === "running" ? `Extracting ${extraction.completed || 0}/${extraction.total || "…"}` : details[id]?.topic_catalog?.length ? "Refresh topics" : "Extract topics"}</button>{extraction?.status === "failed" && <p role="alert" className="w-full text-red-700">{extraction.error}</p>}</div>; })}
    </div>
    {blueprint?.errors?.length > 0 && <div role="alert" className="text-sm text-red-700 mb-4">{blueprint.errors.join(" ")}</div>}
    {blueprint && <>
      {bookIds.length > 0 && <div className="section-tabs blueprint-book-tabs mb-4" role="tablist" aria-label="Textbooks">{bookIds.map(id => <button type="button" role="tab" key={id} aria-selected={activeBook === id} className={activeBook === id ? "is-active" : ""} onClick={() => { setActiveBook(id); setSearch(""); }} data-testid={`blueprint-book-${id}`}><strong>{details[id]?.original_filename || "Loading textbook…"}</strong><small>{topicUnits(details[id]).length} units / modules</small></button>)}</div>}
      <div className="section-tabs" role="tablist" aria-label="Blueprint sections">{blueprint.sections.map(candidate => { const slots = candidate.groups.flatMap(groupSlots); const assigned = allocations.filter(allocation => allocation.section_id === candidate.id).reduce((total, allocation) => total + allocation.count, 0); const offered = slots.reduce((total, slot) => total + slot.marks, 0); const attempted = candidate.attempt_count ? `${candidate.attempt_count} questions attempted` : candidate.choice_set ? "alternative stream" : `${offered} offered marks`; return <button type="button" role="tab" aria-selected={active === candidate.id} aria-controls={`section-panel-${candidate.id}`} id={`section-tab-${candidate.id}`} key={candidate.id} className={active === candidate.id ? "is-active" : ""} onClick={() => setActive(candidate.id)}><strong>{candidate.title}</strong><small>{assigned}/{slots.length} parts · {attempted}</small></button>; })}</div>
      {section && <div role="tabpanel" id={`section-panel-${section.id}`} aria-labelledby={`section-tab-${section.id}`}>
        <div className="section-navigation"><button type="button" className="qp-btn qp-btn-secondary text-xs" disabled={sectionIndex <= 0} onClick={() => setActive(blueprint.sections[sectionIndex - 1].id)} data-testid="blueprint-previous-section">Previous section</button><span className="text-xs font-bold">Section {sectionIndex + 1} of {blueprint.sections.length}</span><button type="button" className="qp-btn qp-btn-secondary text-xs" disabled={sectionIndex >= blueprint.sections.length - 1} onClick={() => setActive(blueprint.sections[sectionIndex + 1].id)} data-testid="blueprint-next-section">Next section</button></div>
        {section.attempt_count && <p className="text-xs my-3">Attempt {section.attempt_count} of the offered questions.</p>}{section.choice_set && <p className="text-xs my-3">Alternative section: choose one section in {section.choice_set}.</p>}
        <input aria-label="Search selected textbook topics" className="qp-input my-3" placeholder="Search units or subtopics…" value={search} onChange={event => setSearch(event.target.value)} />
        {section.groups.flatMap(group => allocationBuckets(group).map(bucket => {
          const assigned = allocations.filter(allocation => allocation.section_id === section.id && allocation.group_id === group.id && allocation.bucket_id === bucket.id).reduce((total, allocation) => total + allocation.count, 0);
          const isComplete = assigned >= bucket.slots.length;
          const bucketAllocations = allocations.filter(allocation => allocation.section_id === section.id && allocation.group_id === group.id && allocation.bucket_id === bucket.id);
          const heading = bucketHeading(group, bucket);
          const leadIn = bucket.slots[0]?.lead_in;
          return <fieldset key={bucket.id} className={`section-topic-group ${isComplete ? "is-complete" : ""}`}><legend>{heading} · <span className="section-assignment-counter" aria-live="polite"><strong>{assigned}</strong><span>/{bucket.slots.length} assigned</span></span></legend>{bucketAllocations.length > 0 && <p className="text-xs my-2" data-testid={`blueprint-selected-topics-${bucket.id}`}>Selected: {bucketAllocations.map(allocation => { const topic = details[allocation.textbook_id]?.topic_catalog?.find(item => item.topic_id === allocation.topic_id); return `${topic?.parent_topic ? `${topic.parent_topic} › ` : ""}${topic?.name || allocation.topic_id} (${allocation.count})`; }).join(", ")}</p>}{leadIn && <p className="text-xs italic my-2">{leadIn}</p>}{isComplete && <p className="section-assignment-limit" role="status">Assignment target reached. Deselect a topic or reduce its question count to make another selection.</p>}<div className="chapter-selection-scroll blueprint-chapter-scroll" aria-label={`Chapters and subtopics for ${heading}`} data-testid={`blueprint-topic-scroll-${bucket.id}`}>{visibleUnits.map(unit => { const unitKey = `${section.id}:${group.id}:${bucket.id}:${activeBook}:${unit.topic_id}`; const expanded = Boolean(search) || expandedUnit.has(unitKey); return <div className="blueprint-unit" key={unit.topic_id}>{topicRow(unit, group, bucket, assigned, { subtopics: unit.subtopics })}{unit.subtopics.length > 0 && <><button type="button" className="blueprint-unit-toggle" aria-expanded={expanded} onClick={() => setExpandedUnit(current => { const next = new Set(current); if (next.has(unitKey)) next.delete(unitKey); else next.add(unitKey); return next; })}><span>{expanded ? "Show less" : "Expand more"} · {unit.subtopics.length} subtopics</span><CaretDown size={16} className={expanded ? "rotate-180" : ""} /></button>{expanded && <div className="blueprint-subtopics">{unit.subtopics.map(topic => topicRow(topic, group, bucket, assigned, { parent: unit }))}</div>}</>}</div>; })}{!visibleUnits.length && <p className="text-sm p-3 text-neutral-500">No matching units or subtopics. Extract topics from the selected textbook above.</p>}</div></fieldset>;
        }))}
      </div>}
    </>}
  </section>;
}
