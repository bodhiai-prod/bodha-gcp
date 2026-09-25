import React from "react";

export default function ConfigurationSummary({ preview, loading, error, compact = false }) {
  const summary = preview?.summary;
  return <section className={`qp-card configuration-summary${compact ? " configuration-summary-compact" : ""}`} data-testid="configuration-summary" aria-live="polite" aria-busy={loading}>
    <h2 className="font-bold uppercase text-sm tracking-wide mb-4">Topics &amp; difficulty</h2>
    {loading && <p className="text-xs text-neutral-500">Updating backend summary…</p>}
    {error && <p role="alert" className="text-sm text-red-700">{error}</p>}
    {summary && <dl className="blueprint-summary-facts">
      <div><dt>Selected textbooks</dt><dd>{summary.textbooks.join(", ") || "None"}</dd></div>
      <div><dt>Mapped topics</dt><dd>{summary.mapped_topics}</dd></div>
      <div><dt>Questions</dt><dd>{summary.question_count || "Unspecified"}</dd></div>
      <div><dt>Diagram-enabled allocations</dt><dd>{summary.diagram_allocations}</dd></div>
      <div><dt>Blueprint</dt><dd>{summary.blueprint_label || "Not selected"}</dd></div>
      <div><dt>Structural match</dt><dd className={summary.blueprint_match ? "text-green-700" : "text-amber-700"}>{summary.blueprint_match ? "Validated" : "Not validated"}</dd></div>
      {Object.entries(summary.difficulty).map(([key, count]) => <div key={key}><dt>{{easy: "Simple", medium: "Moderate", hard: "Challenging"}[key]}</dt><dd>{count} questions</dd></div>)}
    </dl>}
    {preview?.errors?.length > 0 && <ul className="summary-issues" role="alert">{preview.errors.map((e, i) => <li key={i}>{e}</li>)}</ul>}
    {preview?.warnings?.length > 0 && <ul className="summary-issues">{preview.warnings.map((e, i) => <li key={i}>{e}</li>)}</ul>}
    {Object.keys(preview?.overrides || {}).filter(k => k !== "unparsed").length > 0 && <details className="mt-4 text-xs"><summary>Recognized instruction overrides</summary><pre className="whitespace-pre-wrap break-words mt-2">{JSON.stringify(preview.overrides, null, 2)}</pre></details>}
    <div className="mt-5 border-t pt-4"><h3 className="text-xs font-bold uppercase mb-2">Cognitive distribution</h3>
      {summary?.cognitive ? Object.entries(summary.cognitive).map(([key, n]) => <div className="flex justify-between text-xs py-1" key={key}><span>{{information: "Direct / knowledge", concept: "Concept / reasoning", application: "Application / numerical"}[key] || key}</span><strong>{n} Qs</strong></div>) : <p className="text-xs text-neutral-500">Unspecified by this blueprint. Actual values appear after generation.</p>}
    </div>
  </section>;
}
