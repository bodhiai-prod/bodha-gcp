import {
  formatPercentage,
  getAvailableFormatMarks,
  reconcileFormatMarks,
} from "./formatMarks";

test("limits a format to the remaining paper marks", () => {
  const formats = [
    { key: "mcq", marks: 15 },
    { key: "short_answer", marks: 12 },
    { key: "long_answer", marks: 20 },
  ];

  expect(getAvailableFormatMarks(formats, "long_answer", 40)).toBe(13);
  expect(formatPercentage(13, 40)).toBe(33);
});

test("reconciles formats in order when total marks decreases", () => {
  const formats = [
    { key: "mcq", marks: 15 },
    { key: "short_answer", marks: 12 },
    { key: "long_answer", marks: 20 },
  ];

  expect(reconcileFormatMarks(formats, 30).map(({ marks }) => marks)).toEqual([15, 12, 3]);
});
