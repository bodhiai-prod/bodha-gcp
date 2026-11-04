import React from "react";

export default function ConfigurationSummary({ preview, loading, error, current = true }) {
  const summary = preview?.summary;
  return <section className="qp-card" data-testid="configuration-summary" aria-live="polite" aria-busy={loading}>
    <div className="flex items-center justify-between gap-3 mb-4">
      <h2 className="font-medium text-base">Paper overview</h2>
      <span className="text-xs text-neutral-500" role="status">{loading ? "Updating…" : summary && !current ? "Last valid overview" : ""}</span>
    </div>
    {error && <p role="alert" className="text-sm text-red-700">{error}</p>}
    {summary && <dl className="blueprint-summary-facts">
      <div><dt>Selected textbooks</dt><dd>{summary.textbooks.join(", ") || "None"}</dd></div>
      <div><dt>Mapped topics</dt><dd>{summary.mapped_topics}</dd></div>
      <div><dt>Questions</dt><dd>{summary.question_count || "Unspecified"}</dd></div>
      <div><dt>Diagram-enabled allocations</dt><dd>{summary.diagram_allocations}</dd></div>
      <div><dt>Blueprint</dt><dd>{summary.blueprint_label || "Not selected"}</dd></div>
      {summary.blueprint_label && <>
        <div><dt>Template</dt><dd className={summary.template_valid ? "text-green-700" : "text-red-700"}>{summary.template_valid ? "Valid" : "Invalid"}</dd></div>
        <div><dt>Topic assignments</dt><dd className={summary.assignments_valid ? "text-green-700" : "text-amber-700"}>{summary.assignments_valid ? "Ready" : "Needs attention"}</dd></div>
      </>}
      {Object.entries(summary.difficulty).map(([key, count]) => <div key={key}><dt>{{easy: "Simple", medium: "Moderate", hard: "Challenging"}[key]}</dt><dd>{count} questions</dd></div>)}
    </dl>}
    {preview?.errors?.length > 0 && <ul className="summary-issues" role="alert">{preview.errors.map((e, i) => <li key={i}>{e}</li>)}</ul>}
    {preview?.warnings?.length > 0 && <ul className="summary-issues">{preview.warnings.map((e, i) => <li key={i}>{e}</li>)}</ul>}
    {Object.keys(preview?.overrides || {}).filter(k => k !== "unparsed").length > 0 && <details className="mt-4 text-xs"><summary>Recognized instruction overrides</summary><pre className="whitespace-pre-wrap break-words mt-2">{JSON.stringify(preview.overrides, null, 2)}</pre></details>}
    <div className="mt-5 border-t pt-4"><h3 className="text-xs font-medium mb-2">Cognitive distribution</h3>
      {summary?.cognitive ? Object.entries(summary.cognitive).map(([key, n]) => <div className="flex justify-between text-xs py-1" key={key}><span>{{information: "Direct / knowledge", concept: "Concept / reasoning", application: "Application / numerical"}[key] || key}</span><strong>{n} Qs</strong></div>) : <p className="text-xs text-neutral-500">Unspecified by this blueprint. Actual values appear after generation.</p>}
    </div>
  </section>;
}
