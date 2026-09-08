import { apportionCounts } from "./questionCounts";

test("percentage previews sum to the requested count and preserve zero", () => {
  for (let total = 1; total <= 200; total += 1) {
    const counts = apportionCounts({ mcq: 40, long_answer: 40, short_answer: 20, true_false: 0 }, total);
    expect(Object.values(counts).reduce((n, count) => n + count, 0)).toBe(total);
    expect(counts.true_false).toBe(0);
  }
});

test("rounding matches the backend and supports custom formats", () => {
  expect(apportionCounts({ mcq: 40, long_answer: 40, case_study: 20 }, 13))
    .toEqual({ mcq: 5, long_answer: 5, case_study: 3 });
  expect(apportionCounts({ mcq: 0 }, 13)).toEqual({});
});
