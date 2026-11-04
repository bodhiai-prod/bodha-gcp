import React from "react";

const names = { easy: "Simple", medium: "Moderate", hard: "Challenging", unspecified: "Unspecified", information: "Information based", concept: "Concept based", application: "Application based" };
export default function PaperSummary({ paper, editing }) {
  const s = paper.summary;
  const textbooks = paper.source_textbooks || [];
  return <aside className="paper-summary no-print" aria-label="Paper summary" data-testid="paper-summary">
    <details open className="qp-card">
      <summary className="font-medium text-sm">Paper summary</summary>
      {!s ? <p className="text-sm mt-4">Summary unavailable. Reload the paper to retry.</p> : <>
        <p className="text-xs text-neutral-500 mt-3">{s.blueprint ? `${s.blueprint.label} · v${s.blueprint.version}` : "Custom paper"}</p>
        {editing && <p className="text-xs text-amber-700 mt-2">Values describe the saved paper. Save edits to refresh.</p>}
        <div className="paper-summary-totals"><div><small>Attemptable marks</small><strong>{s.offered_marks}</strong><small>Target: {s.target_marks}</small></div><div><small>Questions</small><strong>{s.question_count}</strong><small>Target: {s.target_question_count || "Unknown"}</small></div><div><small>Minutes</small><strong>{s.duration_minutes}</strong></div></div>
        {s.printed_marks !== s.offered_marks && <p className="text-xs mb-4">Printed marks: {s.printed_marks} (includes alternative sections)</p>}
        <h3>Section marks share</h3>
        {s.sections.map((section, i) => <div className="summary-meter" key={`${section.id}-${i}`}><div><span>{section.title} ({section.count} Qs)</span><strong>{section.marks} M</strong></div><progress max={s.offered_marks || 1} value={section.marks} /></div>)}
        <p className="text-xs text-neutral-500">Section shares include printed alternative sections; the total above is what a student can attempt.</p>
        {[['Cognitive domains', s.cognitive], ['Difficulty mix', s.difficulty]].map(([title, rows]) => <section className="mt-5" key={title}><h3>{title}</h3>{rows.filter(row => row.count).map(row => <div className="summary-meter" key={row.key}><div><span>{names[row.key]}</span><strong>{row.marks} M ({s.offered_marks ? Math.round(row.marks / s.offered_marks * 100) : 0}%)</strong></div><progress max={s.offered_marks || 1} value={row.marks} /></div>)}</section>)}
        <section className="mt-5"><h3>Special features</h3><dl className="blueprint-summary-facts">
          <div><dt>Diagrams requested</dt><dd>{s.diagrams.requested}</dd></div><div><dt>Ready / pending / failed</dt><dd>{s.diagrams.ready} / {s.diagrams.pending} / {s.diagrams.failed}</dd></div>
          <div><dt>Internal OR choices</dt><dd>{s.internal_choices}{s.choices_unspecified ? ` (+ ${s.choices_unspecified} unclassified questions)` : ""}</dd></div>
        </dl></section>
        <p className="font-medium text-xs mt-5">{s.status === "ready" ? "Ready" : s.status}</p>
        {s.mismatches.length > 0 && <details className="mt-2"><summary className="text-xs text-amber-800">{s.mismatches.length} requirement mismatches</summary><ul className="summary-issues">{s.mismatches.map((m, i) => <li key={i}>{m}</li>)}</ul></details>}
        <section className="mt-5"><h3>Ingested textbook{textbooks.length === 1 ? "" : "s"}</h3>
          {textbooks.length === 1 ? <ul className="paper-summary-books is-single"><li className="paper-summary-book">{textbooks[0]}</li></ul> : textbooks.length > 1 ? <ol className="paper-summary-books is-multiple">{textbooks.map((textbook, index) => <li className="paper-summary-book" key={`${textbook}-${index}`}>{textbook}</li>)}</ol> : <p className="text-xs text-neutral-500">No source textbook details available.</p>}
        </section>
      </>}
    </details>
  </aside>;
}
