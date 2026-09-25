export const formatMark = (value) => {
  const number = Number(value);
  if (!Number.isFinite(number)) return String(value ?? 0);
  return Number.isInteger(number) ? String(number) : String(number);
};

export const DEFAULT_PAPER_INSTRUCTIONS = "Answer all questions.";
