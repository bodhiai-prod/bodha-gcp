import React, { useEffect, useState } from "react";

const clamp = (value, minimum, maximum) => Math.min(maximum, Math.max(minimum, value));

export default function QuestionCountStepper({ value, min = 1, max, onChange, label }) {
  const [draft, setDraft] = useState(String(value));

  useEffect(() => setDraft(String(value)), [value]);

  const commit = (raw = draft) => {
    const parsed = Number(raw);
    if (!Number.isInteger(parsed)) {
      setDraft(String(value));
      return;
    }
    const next = clamp(parsed, min, max);
    setDraft(String(next));
    if (next !== value) onChange(next);
  };

  return <div className="question-stepper" role="group" aria-label={label}>
    <button type="button" aria-label={`Decrease ${label}`} disabled={value <= min} onClick={() => onChange(value - 1)}>−</button>
    <input
      type="number"
      min={min}
      max={max}
      step={1}
      inputMode="numeric"
      aria-label={`Type ${label}`}
      value={draft}
      onChange={(event) => setDraft(event.target.value)}
      onBlur={() => commit()}
      onKeyDown={(event) => {
        if (event.key === "Enter") {
          event.preventDefault();
          commit(event.currentTarget.value);
          event.currentTarget.blur();
        }
        if (event.key === "Escape") {
          setDraft(String(value));
          event.currentTarget.blur();
        }
      }}
    />
    <button type="button" aria-label={`Increase ${label}`} disabled={value >= max} onClick={() => onChange(value + 1)}>+</button>
  </div>;
}
