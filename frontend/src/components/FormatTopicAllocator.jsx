import React, { useEffect, useMemo, useState } from "react";
import { CaretDown } from "@phosphor-icons/react";
import QuestionCountStepper from "@/components/QuestionCountStepper";
import { countDiagramSelections, MAX_DIAGRAMS_PER_PAPER } from "@/lib/diagramSelection";
import { catalogTopics, generateFormatTopicAllocations } from "@/lib/autoTopicAllocation";

const DIFFICULTY_LABELS = { easy: "Simple", medium: "Moderate", hard: "Challenging" };

function unitsFor(detail) {
  const rows = detail?.topic_catalog || [];
  return rows.filter((row) => !row.parent_topic).map((parent) => ({
    ...parent,
    subtopics: rows.filter((row) => row.parent_topic === parent.name),
  })).filter((row) => row.name);
}

export function allocationStatus(formats, allocations) {
  const expected = Object.fromEntries(formats.map((format) => [format.key, format.count]));
  const actual = allocations.reduce((result, row) => ({
    ...result, [row.format]: (result[row.format] || 0) + row.count,
  }), {});
  const complete = formats.length > 0 && formats.every((format) => actual[format.key] === format.count)
    && Object.keys(actual).every((key) => Object.hasOwn(expected, key));
  return { expected, actual, complete };
}

export default function FormatTopicAllocator({ formats, allocations, onChange, bookIds, details, difficulty, onExtract, onBack, onConfirm }) {
  const [formatIndex, setFormatIndex] = useState(0);
  const [activeBook, setActiveBook] = useState(bookIds[0] || "");
  const [expanded, setExpanded] = useState(() => new Set());
  const [search, setSearch] = useState("");
  useEffect(() => setFormatIndex((current) => Math.min(current, Math.max(0, formats.length - 1))), [formats.length]);
  useEffect(() => setActiveBook((current) => bookIds.includes(current) ? current : bookIds[0] || ""), [bookIds]);
  const format = formats[formatIndex];
  const status = allocationStatus(formats, allocations);
  const selectedDiagrams = countDiagramSelections(allocations);
  const diagramLimitReached = selectedDiagrams >= MAX_DIAGRAMS_PER_PAPER;
  const assigned = status.actual[format?.key] || 0;
  const remaining = Math.max(0, (format?.count || 0) - assigned);
  const units = useMemo(() => unitsFor(details[activeBook]), [activeBook, details]);
  const canAutoAssign = catalogTopics(bookIds, details).length > 0;
  const formatAllocations = allocations.filter((row) => row.format === format?.key);
  const matches = (row) => `${row.name} ${row.parent_topic || ""}`.toLowerCase().includes(search.toLowerCase());
  const visible = units.map((unit) => ({ ...unit, subtopics: unit.subtopics.filter(matches) }))
    .filter((unit) => matches(unit) || unit.subtopics.length);

  if (!format) return <p className="text-sm text-red-700">Add at least one active question format before assigning topics.</p>;

  const expandSelectedUnits = (rows, formatKey = format.key) => {
    const keys = rows.filter((row) => row.format === formatKey).flatMap((row) => {
      const catalog = details[row.textbook_id]?.topic_catalog || [];
      const topic = catalog.find((item) => item.topic_id === row.topic_id);
      const unit = topic?.parent_topic
        ? catalog.find((item) => !item.parent_topic && item.name === topic.parent_topic)
        : topic;
      return unit ? [`${formatKey}:${row.textbook_id}:${unit.topic_id}`] : [];
    });
    if (keys.length) setExpanded((current) => new Set([...current, ...keys]));
  };

  const autoAssign = () => {
    const next = generateFormatTopicAllocations(formats, bookIds, details);
    onChange(next);
    expandSelectedUnits(next);
  };

  const updateRow = (topic, patch) => {
    const matchesRow = (row) => row.format === format.key && row.textbook_id === topic.textbook_id && row.topic_id === topic.topic_id;
    const current = allocations.find(matchesRow);
    if (patch === null) return onChange(allocations.filter((row) => !matchesRow(row)));
    if (!current && remaining <= 0) return;
    const next = current ? allocations.map((row) => matchesRow(row) ? { ...row, ...patch } : row) : [
      ...allocations,
      { format: format.key, textbook_id: topic.textbook_id, topic_id: topic.topic_id, name: topic.name,
        parent_topic: topic.parent_topic || "", count: 1, difficulty: null, diagrams: false, ...patch },
    ];
    onChange(next);
  };

  const topicRow = (topic) => {
    const row = allocations.find((item) => item.format === format.key && item.textbook_id === topic.textbook_id && item.topic_id === topic.topic_id);
    const max = (row?.count || 0) + remaining;
    return <div className={`section-topic-row ${row ? "is-selected" : ""}`} key={topic.topic_id}>
      <label className="section-topic-title">
        <input type="checkbox" checked={Boolean(row)} disabled={!row && remaining === 0} onChange={(event) => updateRow(topic, event.target.checked ? {} : null)} />
        <span><strong>{topic.name}</strong>{topic.parent_topic && <small>{topic.parent_topic}</small>}</span>
      </label>
      {row && <div className="section-topic-controls">
        <label>Difficulty<select value={row.difficulty || ""} aria-label={`Difficulty for ${topic.name} in ${format.label}`} onChange={(event) => updateRow(topic, { difficulty: event.target.value || null })}>
          <option value="">Default ({DIFFICULTY_LABELS[difficulty]})</option>
          {Object.entries(DIFFICULTY_LABELS).map(([key, label]) => <option key={key} value={key}>{label}</option>)}
        </select></label>
        <label className="diagram-toggle"><input type="checkbox" checked={Boolean(row.diagrams)} disabled={!row.diagrams && diagramLimitReached} onChange={(event) => {
          if (event.target.checked && diagramLimitReached) return;
          updateRow(topic, { diagrams: event.target.checked });
        }} />At least one diagram</label>
        <div className="question-stepper-field"><span>Questions</span><QuestionCountStepper value={row.count} max={max} onChange={(count) => updateRow(topic, { count })} label={`questions for ${topic.name} in ${format.label}`} /></div>
      </div>}
    </div>;
  };

  return <div data-testid="format-topic-allocator">
    <div className="flex items-start justify-between gap-3 mb-3">
      <div><div className="overline">// Assign topics by format</div><strong className="block mt-1">{format.label}</strong></div>
      <div className="flex items-center gap-2"><button type="button" className="qp-btn qp-btn-secondary text-xs" disabled={!canAutoAssign} onClick={autoAssign} data-testid="format-auto-assign">Auto assign</button><span className={`qp-badge ${assigned === format.count ? "qp-badge-success" : "qp-badge-red"}`}>{format.label}: {assigned}/{format.count} filled</span></div>
    </div>
    {!canAutoAssign && bookIds.length > 0 && <p className="text-xs text-neutral-500 mb-3">Extract topics from a selected textbook before auto assigning.</p>}
    <p className={`diagram-selection-status ${diagramLimitReached ? "is-limit" : ""}`} role={diagramLimitReached ? "alert" : "status"}>
      {selectedDiagrams} diagram{selectedDiagrams === 1 ? "" : "s"} selected{diagramLimitReached ? " — maximum reached" : ""}
    </p>
    <div className="section-tabs mb-4" role="tablist" aria-label="Question formats">
      {formats.map((item, index) => <button type="button" role="tab" aria-selected={index === formatIndex} className={index === formatIndex ? "is-active" : ""} key={item.key} onClick={() => { setFormatIndex(index); expandSelectedUnits(allocations, item.key); }}><strong>{item.label}</strong><small>{status.actual[item.key] || 0}/{item.count}</small></button>)}
    </div>
    {formatAllocations.length > 0 && <div className="border border-neutral-200 bg-neutral-50 p-2 mb-3 text-xs" data-testid="format-selected-topics">
      <strong className="block mb-1">Selected topics for {format.label}</strong>
      <div className="flex flex-wrap gap-1">{formatAllocations.map((row) => <span key={`${row.textbook_id}:${row.topic_id}`} className="border border-neutral-300 bg-white px-2 py-1">{row.parent_topic ? `${row.parent_topic} › ${row.name}` : row.name} · {row.count}</span>)}</div>
    </div>}
    {!bookIds.length && <p className="text-sm">Select a textbook to load its topics.</p>}
    {bookIds.length > 0 && <div className="section-tabs blueprint-book-tabs mb-3" role="tablist" aria-label="Textbooks">
      {bookIds.map((id) => <button type="button" role="tab" aria-selected={activeBook === id} className={activeBook === id ? "is-active" : ""} key={id} onClick={() => { setActiveBook(id); setSearch(""); }}><strong>{details[id]?.original_filename || "Loading textbook…"}</strong><small>{unitsFor(details[id]).length} units / modules</small></button>)}
    </div>}
    {activeBook && <div className="flex items-center gap-2 mb-3"><input className="qp-input" placeholder="Search units or subtopics…" value={search} onChange={(event) => setSearch(event.target.value)} /><button type="button" className="qp-btn qp-btn-secondary text-xs" onClick={() => onExtract(activeBook)}>Refresh topics</button></div>}
    <div className="chapter-selection-scroll blueprint-chapter-scroll" aria-label={`Topics for ${format.label}`}>
      {visible.map((unit) => { const key = `${format.key}:${activeBook}:${unit.topic_id}`; const open = Boolean(search) || expanded.has(key); return <div className="blueprint-unit" key={unit.topic_id}>{topicRow(unit)}{unit.subtopics.length > 0 && <><button type="button" className="blueprint-unit-toggle" aria-expanded={open} onClick={() => setExpanded((current) => { const next = new Set(current); if (next.has(key)) next.delete(key); else next.add(key); return next; })}><span>{open ? "Show less" : "Expand more"} · {unit.subtopics.length} subtopics</span><CaretDown size={16} className={open ? "rotate-180" : ""} /></button>{open && <div className="blueprint-subtopics">{unit.subtopics.map(topicRow)}</div>}</>}</div>; })}
      {!visible.length && <p className="text-sm p-3 text-neutral-500">No topics are available. Extract topics from the selected textbook.</p>}
    </div>
    {assigned > format.count && <p role="alert" className="text-sm text-red-700 mt-3">Reduce this format by {assigned - format.count} question{assigned - format.count === 1 ? "" : "s"}.</p>}
    <div className="section-navigation mt-4">
      <button type="button" className="qp-btn qp-btn-secondary" onClick={formatIndex === 0 ? onBack : () => setFormatIndex(formatIndex - 1)}>{formatIndex === 0 ? "Back to mix" : "Previous format"}</button>
      <span className="text-xs font-medium">Format {formatIndex + 1} of {formats.length}</span>
      {formatIndex < formats.length - 1
        ? <button type="button" className="qp-btn qp-btn-primary" disabled={assigned !== format.count} onClick={() => setFormatIndex(formatIndex + 1)}>Next format</button>
        : <button type="button" className="qp-btn qp-btn-primary" disabled={!status.complete} onClick={onConfirm}>Confirm topics</button>}
    </div>
  </div>;
}
