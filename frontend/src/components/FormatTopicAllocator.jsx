import React, { useEffect, useMemo, useState } from "react";
import { CaretDown } from "@phosphor-icons/react";

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
  const [expanded, setExpanded] = useState(null);
  const [search, setSearch] = useState("");
  useEffect(() => setFormatIndex((current) => Math.min(current, Math.max(0, formats.length - 1))), [formats.length]);
  useEffect(() => setActiveBook((current) => bookIds.includes(current) ? current : bookIds[0] || ""), [bookIds]);
  const format = formats[formatIndex];
  const status = allocationStatus(formats, allocations);
  const assigned = status.actual[format?.key] || 0;
  const remaining = Math.max(0, (format?.count || 0) - assigned);
  const units = useMemo(() => unitsFor(details[activeBook]), [activeBook, details]);
  const matches = (row) => `${row.name} ${row.parent_topic || ""}`.toLowerCase().includes(search.toLowerCase());
  const visible = units.map((unit) => ({ ...unit, subtopics: unit.subtopics.filter(matches) }))
    .filter((unit) => matches(unit) || unit.subtopics.length);

  if (!format) return <p className="text-sm text-red-700">Add at least one active question format before assigning topics.</p>;

  const updateRow = (topic, patch) => {
    const matchesRow = (row) => row.format === format.key && row.textbook_id === topic.textbook_id && row.topic_id === topic.topic_id;
    const current = allocations.find(matchesRow);
    if (patch === null) return onChange(allocations.filter((row) => !matchesRow(row)));
    if (!current && remaining <= 0) return;
    const next = current ? allocations.map((row) => matchesRow(row) ? { ...row, ...patch } : row) : [
      ...allocations,
      { format: format.key, textbook_id: topic.textbook_id, topic_id: topic.topic_id, name: topic.name,
        parent_topic: topic.parent_topic || "", count: 1, difficulty: null, ...patch },
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
        <div className="question-stepper-field"><span>Questions</span><div className="question-stepper" role="group" aria-label={`Question count for ${topic.name} in ${format.label}`}>
          <button type="button" disabled={row.count <= 1} onClick={() => updateRow(topic, { count: row.count - 1 })}>−</button>
          <output>{row.count}</output>
          <button type="button" disabled={row.count >= max} onClick={() => updateRow(topic, { count: row.count + 1 })}>+</button>
        </div></div>
      </div>}
    </div>;
  };

  return <div data-testid="format-topic-allocator">
    <div className="flex items-start justify-between gap-3 mb-3">
      <div><div className="overline">// ASSIGN TOPICS BY FORMAT</div><strong className="block mt-1">{format.label}</strong></div>
      <span className={`qp-badge ${assigned === format.count ? "qp-badge-success" : "qp-badge-red"}`}>{assigned}/{format.count} assigned</span>
    </div>
    <div className="section-tabs mb-4" role="tablist" aria-label="Question formats">
      {formats.map((item, index) => <button type="button" role="tab" aria-selected={index === formatIndex} className={index === formatIndex ? "is-active" : ""} key={item.key} onClick={() => setFormatIndex(index)}><strong>{item.label}</strong><small>{status.actual[item.key] || 0}/{item.count}</small></button>)}
    </div>
    {!bookIds.length && <p className="text-sm">Select a textbook to load its topics.</p>}
    {bookIds.length > 0 && <div className="section-tabs blueprint-book-tabs mb-3" role="tablist" aria-label="Textbooks">
      {bookIds.map((id) => <button type="button" role="tab" aria-selected={activeBook === id} className={activeBook === id ? "is-active" : ""} key={id} onClick={() => { setActiveBook(id); setSearch(""); }}><strong>{details[id]?.original_filename || "Loading textbook…"}</strong><small>{unitsFor(details[id]).length} units / modules</small></button>)}
    </div>}
    {activeBook && <div className="flex items-center gap-2 mb-3"><input className="qp-input" placeholder="Search units or subtopics…" value={search} onChange={(event) => setSearch(event.target.value)} /><button type="button" className="qp-btn qp-btn-secondary text-xs" onClick={() => onExtract(activeBook)}>Refresh topics</button></div>}
    <div className="chapter-selection-scroll blueprint-chapter-scroll" aria-label={`Topics for ${format.label}`}>
      {visible.map((unit) => { const key = `${format.key}:${activeBook}:${unit.topic_id}`; const open = Boolean(search) || expanded === key; return <div className="blueprint-unit" key={unit.topic_id}>{topicRow(unit)}{unit.subtopics.length > 0 && <><button type="button" className="blueprint-unit-toggle" aria-expanded={open} onClick={() => setExpanded((current) => current === key ? null : key)}><span>{open ? "Show less" : "Expand more"} · {unit.subtopics.length} subtopics</span><CaretDown size={16} className={open ? "rotate-180" : ""} /></button>{open && <div className="blueprint-subtopics">{unit.subtopics.map(topicRow)}</div>}</>}</div>; })}
      {!visible.length && <p className="text-sm p-3 text-neutral-500">No topics are available. Extract topics from the selected textbook.</p>}
    </div>
    {assigned > format.count && <p role="alert" className="text-sm text-red-700 mt-3">Reduce this format by {assigned - format.count} question{assigned - format.count === 1 ? "" : "s"}.</p>}
    <div className="section-navigation mt-4">
      <button type="button" className="qp-btn qp-btn-secondary" onClick={formatIndex === 0 ? onBack : () => setFormatIndex(formatIndex - 1)}>{formatIndex === 0 ? "Back to mix" : "Previous format"}</button>
      <span className="text-xs font-bold">Format {formatIndex + 1} of {formats.length}</span>
      {formatIndex < formats.length - 1
        ? <button type="button" className="qp-btn qp-btn-primary" disabled={assigned !== format.count} onClick={() => setFormatIndex(formatIndex + 1)}>Next format</button>
        : <button type="button" className="qp-btn qp-btn-primary" disabled={!status.complete} onClick={onConfirm}>Confirm topics</button>}
    </div>
  </div>;
}
