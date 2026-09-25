import { DEFAULT_PAPER_INSTRUCTIONS, formatMark } from "./exportFormat";

test("formats marks without incidental decimal zeroes", () => {
  expect(formatMark(2.0)).toBe("2");
  expect(formatMark("3.0")).toBe("3");
  expect(formatMark(2.5)).toBe("2.5");
});

test("exposes the stable default paper instruction", () => {
  expect(DEFAULT_PAPER_INSTRUCTIONS).toBe("Answer all questions.");
});
