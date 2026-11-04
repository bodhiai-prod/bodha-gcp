import React from "react";
import { Link } from "react-router-dom";
import { ArrowLeft } from "@phosphor-icons/react";
import PaperSummary from "@/components/PaperSummary";

export function PaperWorkspaceMain({ children, testId }) {
  return <main className="max-w-7xl mx-auto p-6 md:p-12" data-testid={testId}>{children}</main>;
}

export function PaperWorkspaceToolbar({ backTo, backLabel, backTestId, children }) {
  return <div className="no-print flex items-center justify-between mb-8 flex-wrap gap-3">
    <Link to={backTo} className="qp-btn-ghost inline-flex items-center gap-2" data-testid={backTestId}>
      <ArrowLeft size={16} weight="bold" /> {backLabel}
    </Link>
    <div className="flex flex-wrap gap-2">{children}</div>
  </div>;
}

export function PaperWorkspaceContent({ paper, editing = false, children }) {
  return <div className="paper-with-summary">
    {children}
    <PaperSummary paper={paper} editing={editing} />
  </div>;
}
