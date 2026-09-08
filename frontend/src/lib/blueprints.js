// Preset identities only. The backend catalog owns structure and prompt prose.
export const BLUEPRINT_PRESETS = [
  {
    id: "icse10",
    label: "Use ICSE Class 10 (80m / 2h)",
    testId: "blueprint-preset-icse",
  },
  {
    id: "cbse10",
    label: "Use CBSE Class 10 (80m / 3h)",
    testId: "blueprint-preset-cbse",
  },
  {
    id: "isc12",
    label: "Use ISC Class 12 (80m / 3h)",
    testId: "blueprint-preset-isc-12",
  },
  {
    id: "cbse12",
    label: "Use CBSE Class 12 (80m / 3h)",
    testId: "blueprint-preset-cbse-12",
  },
];

/**
 * Resolve a blueprint template into the text that goes in the textarea.
 * {SUBJECT} is replaced by the upper-cased subject when one is known; when it
 * is not, the token is left in place so the teacher can type over it.
 */
export function fillBlueprint(template, { subject, year } = {}) {
  const trimmed = (subject || "").trim();
  let out = template.replace(/\{YEAR\}/g, String(year || new Date().getFullYear()));
  if (trimmed) out = out.replace(/\{SUBJECT\}/g, trimmed.toUpperCase());
  return out;
}
