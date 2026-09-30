// Only whitespace can separate inline list items; never split inside a number.
export const instructionLines = (instructions = "") =>
  instructions
    .split(/\r?\n/)
    .flatMap(line => line.split(/\s+(?=(?:\([ivxlcdm]+\)|\d+[.)]|[-•])\s)/i))
    .map(line => line.trim())
    .filter(Boolean);
