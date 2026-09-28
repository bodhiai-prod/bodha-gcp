export const QUESTION_TYPE_LABELS = {
  information: "Information Based",
  concept: "Concept Based",
  application: "Application Based",
};

const aliases = {
  information: "information", info: "information", knowledge: "information", direct: "information",
  concept: "concept", conceptual: "concept", reasoning: "concept", problem: "concept",
  application: "application", applied: "application", numerical: "application",
};

export const canonicalQuestionType = (value, fallback = "concept") =>
  aliases[String(value || "").trim().toLowerCase().replace(/[ -]+/g, "_")] || fallback;

export const questionTypeLabel = (value) =>
  QUESTION_TYPE_LABELS[canonicalQuestionType(value)] || QUESTION_TYPE_LABELS.concept;
