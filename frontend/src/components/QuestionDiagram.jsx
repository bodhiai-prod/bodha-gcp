import React, { useEffect, useState } from "react";
import { API } from "@/lib/api";
import { getFirebaseToken } from "@/lib/firebase";
import { Image as ImageIcon } from "@phosphor-icons/react";

// Fetches the diagram as a blob URL so the auth token can be sent via header.
const DiagramImage = ({ paperId, questionId }) => {
  const [src, setSrc] = useState(null);
  useEffect(() => {
    let revoked = false;
    let currentUrl = null;
    getFirebaseToken().then((token) => fetch(`${API}/papers/${paperId}/diagrams/${questionId}`, {
      headers: token ? { Authorization: `Bearer ${token}` } : {},
    }))
      .then((r) => (r.ok ? r.blob() : null))
      .then((blob) => {
        if (!blob || revoked) return;
        currentUrl = URL.createObjectURL(blob);
        setSrc(currentUrl);
      })
      .catch(() => {});
    return () => {
      revoked = true;
      if (currentUrl) URL.revokeObjectURL(currentUrl);
    };
  }, [paperId, questionId]);
  if (!src) {
    return (
      <div
        className="w-48 h-48 border-2 border-dashed border-neutral-300 flex items-center justify-center text-neutral-400 text-xs"
        data-testid="diagram-loading"
      >
        <ImageIcon size={18} /> &nbsp; diagram loading...
      </div>
    );
  }
  return (
    <img
      src={src}
      alt="diagram"
      className="max-w-xs border border-neutral-300 bg-white"
      data-testid={`diagram-${questionId}`}
    />
  );
};

/**
 * A question's diagram in whichever state it happens to be in — rendered,
 * still generating on the backend, or absent.
 *
 * Owned by one component so the question paper and the answer key always show
 * the same figure. That matters more than it looks: the downloaded PDF is now
 * the browser's rendering of this markup (see lib/print.js), so anything a page
 * fails to render is missing from its PDF too.
 *
 * The `data-testid`s here are load-bearing — the print stylesheet in index.css
 * targets them to hide the placeholders and to keep diagrams whole across page
 * breaks.
 */
export default function QuestionDiagram({ paperId, question }) {
  if (question?.diagram_path) {
    return (
      <div className="mt-3 pl-6">
        <DiagramImage paperId={paperId} questionId={question.id} />
      </div>
    );
  }
  if (question?.diagram_status === "pending") {
    return (
      <div className="mt-3 pl-6">
        <div
          className="w-48 h-32 border-2 border-dashed border-neutral-300 flex items-center justify-center text-neutral-500 text-xs bg-neutral-50"
          data-testid={`diagram-pending-${question.id}`}
        >
          <ImageIcon size={18} /> &nbsp; generating diagram...
        </div>
      </div>
    );
  }
  return null;
}
