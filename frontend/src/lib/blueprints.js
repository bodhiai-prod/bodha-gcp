// Preset identities only. The backend catalog owns structure and prompt prose.
export const BLUEPRINT_PRESETS = [
  {
    family: "icse10",
    label: "ICSE Class 10",
    testId: "blueprint-preset-icse",
  },
  {
    family: "cbse10",
    label: "CBSE Class 10",
    testId: "blueprint-preset-cbse",
  },
  {
    family: "isc12",
    label: "ISC Class 12",
    testId: "blueprint-preset-isc-12",
  },
  {
    family: "cbse12",
    label: "CBSE Class 12",
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
