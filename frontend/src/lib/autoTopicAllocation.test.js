import { catalogTopics, generateBlueprintTopicAllocations, generateFormatTopicAllocations } from "./autoTopicAllocation";

const random = () => 0;
const details = {
  first: { topic_catalog: [
    { textbook_id: "first", topic_id: "algebra", name: "Algebra", parent_topic: "" },
    { textbook_id: "first", topic_id: "linear", name: "Linear equations", parent_topic: "Algebra" },
  ] },
  second: { topic_catalog: [
    { textbook_id: "second", topic_id: "motion", name: "Motion", parent_topic: "" },
  ] },
};

test("catalog topics includes parent topics and subtopics from every selected book", () => {
  expect(catalogTopics(["first", "second"], details).map((topic) => topic.topic_id)).toEqual([
    "algebra", "linear", "motion",
  ]);
});

test("format auto assignment exactly fills every format before reusing topics", () => {
  const rows = generateFormatTopicAllocations([
    { key: "mcq", count: 2 }, { key: "short_answer", count: 2 },
  ], ["first", "second"], details, random);
  expect(rows.reduce((total, row) => total + row.count, 0)).toBe(4);
  expect(rows.filter((row) => row.format === "mcq").reduce((total, row) => total + row.count, 0)).toBe(2);
  expect(rows.filter((row) => row.format === "short_answer").reduce((total, row) => total + row.count, 0)).toBe(2);
  expect(new Set(rows.map((row) => `${row.textbook_id}:${row.topic_id}`)).size).toBe(3);
  expect(new Set(rows.slice(0, 3).map((row) => `${row.textbook_id}:${row.topic_id}`)).size).toBe(3);
  expect(rows.every((row) => row.difficulty === null && row.diagrams === false)).toBe(true);
});

test("blueprint auto assignment fills each bucket exactly and reuses catalog topics when needed", () => {
  const blueprint = { sections: [{ id: "A", groups: [
    { id: "one", count: 2, marks: 1, format: "mcq" },
    { id: "two", count: 3, marks: 3, format: "short_answer" },
  ] }] };
  const rows = generateBlueprintTopicAllocations(blueprint, ["first"], details, random);
  expect(rows.reduce((total, row) => total + row.count, 0)).toBe(5);
  expect(rows.filter((row) => row.bucket_id === "one:one-1").reduce((total, row) => total + row.count, 0)).toBe(2);
  expect(rows.filter((row) => row.bucket_id === "two:two-1").reduce((total, row) => total + row.count, 0)).toBe(3);
  expect(rows.every((row) => row.textbook_id === "first")).toBe(true);
});
