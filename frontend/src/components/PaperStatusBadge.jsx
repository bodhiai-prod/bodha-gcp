import React from "react";
import {
  CheckCircle,
  CircleNotch,
  WarningCircle,
  XCircle,
} from "@phosphor-icons/react";

const STATUS_STYLE = {
  generating: { className: "qp-badge-blue", Icon: CircleNotch, spinning: true },
  updating: { className: "qp-badge-blue", Icon: CircleNotch, spinning: true },
  ready: { className: "qp-badge-success", Icon: CheckCircle },
  needs_review: { className: "qp-badge-yellow", Icon: WarningCircle },
  generation_failed: { className: "qp-badge-red", Icon: XCircle },
  update_failed: { className: "qp-badge-red", Icon: XCircle },
  generation_cancelled: { className: "qp-badge-red", Icon: XCircle },
};

export default function PaperStatusBadge({ status, testid }) {
  if (!status) return null;

  const presentation = STATUS_STYLE[status.key] || STATUS_STYLE.ready;
  const Icon = presentation.Icon;
  const completed = status.progress?.completed;
  const total = status.progress?.total;
  const progressLabel = Number.isFinite(completed) && Number.isFinite(total) && total > 0
    ? ` ${completed} / ${total}`
    : "";

  return (
    <span
      className={`qp-badge ${presentation.className} shrink-0 whitespace-nowrap`}
      data-testid={testid}
      aria-label={`${status.label}${progressLabel}`}
    >
      <Icon
        size={13}
        weight="bold"
        className={presentation.spinning ? "animate-spin" : undefined}
        aria-hidden="true"
      />
      <span>{status.label}{progressLabel}</span>
    </span>
  );
}
