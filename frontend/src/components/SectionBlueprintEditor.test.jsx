import React, { act } from "react";
import { createRoot } from "react-dom/client";
import SectionBlueprintEditor from "./SectionBlueprintEditor";

test("blueprint auto assign replaces manual rows and fills every bucket", async () => {
  global.IS_REACT_ACT_ENVIRONMENT = true;
  const container = document.createElement("div");
  document.body.appendChild(container);
  const root = createRoot(container);
  const onChange = jest.fn();
  const blueprint = { sections: [{ id: "A", title: "Section A", groups: [
    { id: "questions", count: 2, marks: 1, format: "mcq" },
  ] }] };
  await act(async () => root.render(<SectionBlueprintEditor
    blueprint={blueprint}
    bookIds={["book"]}
    details={{ book: { topic_catalog: [
      { textbook_id: "book", topic_id: "one", name: "One", parent_topic: "" },
      { textbook_id: "book", topic_id: "two", name: "Two", parent_topic: "" },
    ] } }}
    allocations={[{ section_id: "A", group_id: "questions", bucket_id: "questions:questions-1", textbook_id: "book", topic_id: "old", count: 2 }]}
    onChange={onChange}
    difficulty="medium"
    onExtract={jest.fn()}
  />));
  onChange.mockClear();
  await act(async () => container.querySelector('[data-testid="blueprint-auto-assign"]').click());
  const rows = onChange.mock.calls.at(-1)[0];
  expect(rows.reduce((total, row) => total + row.count, 0)).toBe(2);
  expect(rows.some((row) => row.topic_id === "old")).toBe(false);
  act(() => root.unmount());
  container.remove();
});

test("blueprint auto assign is disabled when the catalog is empty", async () => {
  global.IS_REACT_ACT_ENVIRONMENT = true;
  const container = document.createElement("div");
  document.body.appendChild(container);
  const root = createRoot(container);
  const blueprint = { sections: [{ id: "A", title: "Section A", groups: [{ id: "questions", count: 1, marks: 1, format: "mcq" }] }] };
  await act(async () => root.render(<SectionBlueprintEditor
    blueprint={blueprint}
    bookIds={["book"]}
    details={{ book: { topic_catalog: [] } }}
    allocations={[]}
    onChange={jest.fn()}
    difficulty="medium"
    onExtract={jest.fn()}
  />));
  expect(container.querySelector('[data-testid="blueprint-auto-assign"]').disabled).toBe(true);
  expect(container.textContent).toContain("Extract topics from a selected textbook before auto assigning.");
  act(() => root.unmount());
  container.remove();
});
