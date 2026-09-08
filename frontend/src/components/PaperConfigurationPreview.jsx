import React from "react";
import { FileText, CheckCircle } from "@phosphor-icons/react";

export default function PaperConfigurationPreview({ title, textbook, totalMarks, duration, questionCount, selectedTopics, difficulty, formats, counts, blueprint, instructions }) {
  const total = Math.max(1, Number(questionCount) || 0);
  return (
    <aside className="studio-live-column" aria-label="Live paper configuration preview">
      <section className="qp-card studio-live-preview" data-testid="configuration-preview">
        <div className="studio-panel-heading"><h2>Live preview</h2><span className="studio-live-indicator">Auto-updating</span></div>
        <div className="studio-mini-paper">
          <div className="studio-document-icon"><FileText size={23} /></div>
          <p className="studio-eyebrow">QUESTION PAPER</p>
          <h3>{title.trim() || "Your question paper"}</h3>
          <div className="studio-preview-facts">
            <div><span>Subject</span><strong>{textbook?.subject || "Select a textbook"}</strong></div>
            <div><span>Class</span><strong>{textbook?.class_name || "—"}</strong></div>
            <div><span>Total marks</span><strong>{totalMarks || "—"}</strong></div>
            <div><span>Duration</span><strong>{duration || "—"} min</strong></div>
          </div>
          <div className="studio-preview-section"><h4>Topics covered</h4>
            {selectedTopics.length ? <ul>{selectedTopics.slice(0, 5).map((topic) => <li key={topic.key}><CheckCircle size={12} />{topic.name}</li>)}</ul> : <p>Select topics to shape your paper.</p>}
            {selectedTopics.length > 5 && <p>+ {selectedTopics.length - 5} more selected</p>}
          </div>
          <div className="studio-preview-section"><h4>{blueprint ? "Section blueprint" : "Question overview"}</h4>
            {blueprint ? <p className="studio-preview-copy">{blueprint}</p> : <>
              {formats.filter(f => (counts[f.key] || 0) > 0).map(f => <div className="studio-key-value" key={f.key}><span>{f.label}</span><strong>{counts[f.key]} questions</strong></div>)}
              <div className="studio-key-value"><span>Default difficulty</span><strong>{difficulty}</strong></div>
            </>}
          </div>
          {instructions.trim() && <div className="studio-preview-section"><h4>Additional instructions</h4><p className="studio-preview-copy">{instructions}</p></div>}
        </div>
        <p className="studio-preview-note">Configuration preview. Questions appear after generation.</p>
      </section>
      <section className="qp-card" data-testid="configuration-distribution">
        <div className="studio-panel-heading"><h2>Question distribution</h2></div>
        {blueprint ? <p className="studio-muted">Your blueprint determines the final sections and question counts.</p> : <>
          {formats.map(f => <div className="studio-meter-row" key={f.key}>
            <div className="studio-key-value"><span>{f.label}</span><strong>{counts[f.key] || 0}</strong></div>
            <div className="studio-meter"><span style={{ width: `${Math.min(100, ((counts[f.key] || 0) / total) * 100)}%` }} /></div>
          </div>)}
          <div className="studio-panel-total"><span>Total configured</span><strong>{questionCount || 0} questions</strong></div>
        </>}
      </section>
    </aside>
  );
}
