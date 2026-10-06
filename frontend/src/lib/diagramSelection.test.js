import { countDiagramSelections, MAX_DIAGRAMS_PER_PAPER } from "./diagramSelection";

test("counts one selected diagram per enabled allocation row", () => {
  expect(countDiagramSelections([
    { count: 4, diagrams: true },
    { count: 2, diagrams: false },
    { count: 1, diagrams: true },
  ])).toBe(2);
  expect(MAX_DIAGRAMS_PER_PAPER).toBe(5);
});
