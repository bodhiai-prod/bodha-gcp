import { allocationStatus } from "./FormatTopicAllocator";

const formats = [
  { key: "mcq", count: 2 },
  { key: "long_answer", count: 1 },
];

test("allocation status requires an exact count for every active format", () => {
  expect(allocationStatus(formats, [
    { format: "mcq", count: 2 },
    { format: "long_answer", count: 1 },
  ]).complete).toBe(true);
  expect(allocationStatus(formats, [
    { format: "mcq", count: 1 },
    { format: "long_answer", count: 1 },
  ]).complete).toBe(false);
});

test("allocation status rejects stale assignments for inactive formats", () => {
  expect(allocationStatus(formats, [
    { format: "mcq", count: 2 },
    { format: "long_answer", count: 1 },
    { format: "true_false", count: 1 },
  ]).complete).toBe(false);
});
