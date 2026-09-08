import { retainCompatibleAllocations, updateAllocation } from "./sectionBlueprint";

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
