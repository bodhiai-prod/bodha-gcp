import { allocationBuckets, normalizeBucketAllocations, retainCompatibleAllocations, updateAllocation } from "./sectionBlueprint";

const topic = { textbook_id: "book", topic_id: "transformers" };
const bp = { sections: [{ id: "A", groups: [{ id: "main", count: 2, marks: 1, format: "mcq" }] }] };
test("the same topic has independent section settings", () => {
  let rows = updateAllocation([], "A", "main", topic, { difficulty: "hard", diagrams: true });
  rows = updateAllocation(rows, "B", "main", topic, { difficulty: "easy" });
  expect(rows).toHaveLength(2);
  expect(rows[0].diagrams).toBe(true);
  expect(rows[1].diagrams).toBe(false);
  rows = updateAllocation(rows, "A", "main", topic, null);
  expect(rows[0].section_id).toBe("B");
});
test("template switches retain only compatible groups and selected books", () => {
  const rows = updateAllocation([], "A", "main", topic, {});
  expect(retainCompatibleAllocations(rows, bp, bp, ["book"])).toEqual(rows);
  expect(retainCompatibleAllocations(rows, bp, bp, [])).toEqual([]);
  const changed = { sections: [{ id: "A", groups: [{ ...bp.sections[0].groups[0], marks: 2 }] }] };
  expect(retainCompatibleAllocations(rows, bp, changed, ["book"])).toEqual([]);
});
test("identical topic names in different books stay separate", () => {
  let rows = updateAllocation([], "A", "main", topic, {});
  rows = updateAllocation(rows, "A", "main", { ...topic, textbook_id: "other" }, {});
  expect(rows).toHaveLength(2);
  expect(rows[0].difficulty).toBeNull();
});
test("contiguous compatible slots become one picker and legacy slot rows collapse into it", () => {
  const slots = [
    { id: "q20", parent_label: "Question 20", marks: 2, format: "short_answer" },
    { id: "q21", parent_label: "Question 21", marks: 2, format: "short_answer" },
    { id: "q22", parent_label: "Question 22", marks: 5, format: "long_answer" },
  ];
  const blueprint = { sections: [{ id: "A", groups: [{ id: "questions", count: 3, marks: 2, format: "short_answer", slots }] }] };
  expect(allocationBuckets(blueprint.sections[0].groups[0]).map(bucket => [bucket.id, bucket.slots.length])).toEqual([
    ["questions:q20", 2], ["questions:q22", 1],
  ]);
  const rows = slots.slice(0, 2).map(slot => ({ section_id: "A", group_id: "questions", slot_id: slot.id, ...topic, count: 1, difficulty: null, diagrams: false }));
  expect(normalizeBucketAllocations(rows, blueprint)).toEqual([{
    section_id: "A", group_id: "questions", slot_id: null, bucket_id: "questions:q20", ...topic, count: 2, difficulty: null, diagrams: false,
  }]);
});
test("legacy parent-question allocations spread across compatible buckets", () => {
  const slots = [
    { id: "q1-i", parent_id: "q1", parent_label: "Question 1", marks: 1, format: "mcq" },
    { id: "q1-ii", parent_id: "q1", parent_label: "Question 1", marks: 1, format: "mcq" },
    { id: "q1-iii", parent_id: "q1", parent_label: "Question 1", marks: 2, format: "short_answer" },
  ];
  const blueprint = { sections: [{ id: "A", groups: [{ id: "q1", count: 1, marks: 1, format: "mcq", slots }] }] };
  const rows = [{ section_id: "A", group_id: "q1", slot_id: "q1", ...topic, count: 3, difficulty: null, diagrams: false }];
  expect(normalizeBucketAllocations(rows, blueprint).map(row => [row.bucket_id, row.count])).toEqual([
    ["q1:q1-i", 2], ["q1:q1-iii", 1],
  ]);
});
test("legacy multi-count slot rows share bucket capacity instead of looking overfilled", () => {
  const slots = Array.from({ length: 15 }, (_, index) => ({
    id: `q1-${index + 1}`, parent_id: "q1", parent_label: "Question 1", marks: 1, format: "mcq",
  }));
  const blueprint = { sections: [{ id: "A", groups: [{ id: "q1", count: 1, marks: 15, format: "mcq", slots }] }] };
  const rows = [
    { section_id: "A", group_id: "q1", slot_id: "q1-1", ...topic, count: 7, difficulty: null, diagrams: false },
    { section_id: "A", group_id: "q1", slot_id: "q1-2", textbook_id: "book", topic_id: "electrostatics", count: 8, difficulty: null, diagrams: false },
  ];
  expect(normalizeBucketAllocations(rows, blueprint).map(row => [row.bucket_id, row.count])).toEqual([
    ["q1:q1-1", 7], ["q1:q1-1", 8],
  ]);
});
