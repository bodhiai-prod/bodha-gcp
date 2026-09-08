import React from "react";
import { FileText, Books, ChartPieSlice, ChartBar, CheckCircle } from "@phosphor-icons/react";

export default function PaperInsights({ paper, sources, sourcesFailed = false, editing = false }) {
  const questions = (paper.sections || []).flatMap(section => section.questions || []);
  const marks = questions.reduce((sum, question) => sum + (Number(question.marks) || 0), 0);
  const levels = [
    { key: "easy", label: "Easy", color: "#34b894" },
    { key: "medium", label: "Medium", color: "#8271f5" },
    { key: "hard", label: "Hard", color: "#f2ad61" },
    { key: "other", label: "Unspecified", color: "#cbd0db" },
  ].map(level => ({ ...level, count: questions.filter(q => level.key === "other" ? !["easy", "medium", "hard"].includes(q.difficulty) : q.difficulty === level.key).length }));
  let offset = 0;
  const gradient = levels.map(level => {
    const start = offset;
    offset += questions.length ? level.count / questions.length * 100 : 0;
    return `${level.color} ${start}% ${offset}%`;
  }).join(", ");
  const date = paper.created_at ? new Date(paper.created_at) : null;
  const status = paper.topup?.status === "generating" ? "Adding questions" : paper.recovered_from_truncation || (paper.target_question_count || 0) > questions.length ? "Needs review" : "Ready";
  return (
    <aside className="studio-insights no-print" aria-label="Paper insights">
      <section className="qp-card"><div className="studio-panel-heading"><h2><FileText size={16} />Paper metadata</h2></div>
        <dl className="studio-metadata">
          <div><dt>Generated</dt><dd>{date && !Number.isNaN(date.getTime()) ? date.toLocaleDateString(undefined, { year: "numeric", month: "short", day: "numeric" }) : "—"}</dd></div>
          <div><dt>Paper ID</dt><dd title={paper.id}>{paper.id || "—"}</dd></div>
          <div><dt>Total questions</dt><dd data-testid="insight-question-count">{questions.length}</dd></div>
          <div><dt>Total marks</dt><dd>{paper.total_marks}</dd></div>
          <div><dt>Status</dt><dd><span className={`studio-status ${status !== "Ready" ? "is-pending" : ""}`}>{editing ? "Editing draft" : status}</span></dd></div>
        </dl>
      </section>
      <section className="qp-card"><div className="studio-panel-heading"><h2><Books size={16} />Source textbooks</h2></div>
        {sources.map(source => <div key={source.id} className="studio-source"><span className="studio-source-icon"><Books size={17} /></span><div><strong>{source.original_filename}</strong><small>{source.subject} {source.class_name ? `· Class ${source.class_name}` : ""}</small></div></div>)}
        {!sources.length && <p className="studio-muted">{sourcesFailed ? "Source details are unavailable." : (paper.textbook_ids?.length || paper.textbook_id) ? "Loading source details…" : "No source textbooks linked."}</p>}
        {(paper.topics || []).length > 0 && <><p className="studio-eyebrow mt-4">Topics covered</p><ul className="studio-topic-list">{paper.topics.map((topic, index) => <li key={index}><span>{typeof topic === "string" ? topic : topic.name}</span><CheckCircle size={13} /></li>)}</ul></>}
      </section>
      <section className="qp-card"><div className="studio-panel-heading"><h2><ChartPieSlice size={16} />Difficulty breakdown</h2></div>
        <div className="studio-donut" style={{ background: questions.length ? `conic-gradient(${gradient})` : "#e9eaf0" }} role="img" aria-label={levels.map(level => `${level.label}: ${level.count} questions`).join(", ")}><span><strong>{questions.length}</strong><small>questions</small></span></div>
        {levels.filter(level => level.key !== "other" || level.count).map(level => <div className="studio-key-value" key={level.key}><span className="studio-legend"><i style={{ background: level.color }} />{level.label}</span><strong>{questions.length ? Math.round(level.count / questions.length * 100) : 0}%</strong></div>)}
      </section>
      <section className="qp-card"><div className="studio-panel-heading"><h2><ChartBar size={16} />Marks distribution</h2></div>
        {(paper.sections || []).map((section, index) => {
          const sectionMarks = (section.questions || []).reduce((sum, question) => sum + (Number(question.marks) || 0), 0);
          return <div className="studio-meter-row" key={index}><div className="studio-key-value"><span>{section.title}</span><strong>{sectionMarks} marks</strong></div><div className="studio-meter"><span style={{ width: `${marks > 0 ? sectionMarks / marks * 100 : 0}%` }} /></div></div>;
        })}
        <div className="studio-panel-total"><span>Question marks</span><strong>{marks}</strong></div>
      </section>
    </aside>
  );
}
