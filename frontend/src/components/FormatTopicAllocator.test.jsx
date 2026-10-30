import React, { act } from "react";
import { createRoot } from "react-dom/client";
import FormatTopicAllocator, { allocationStatus } from "./FormatTopicAllocator";

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

test("shows the five-diagram warning and blocks another diagram selection", async () => {
  global.IS_REACT_ACT_ENVIRONMENT = true;
  const container = document.createElement("div");
  document.body.appendChild(container);
  const root = createRoot(container);
  const allocations = Array.from({ length: 6 }, (_, index) => ({
    format: "mcq",
    textbook_id: "book",
    topic_id: `topic-${index}`,
    name: `Topic ${index}`,
    count: 1,
    diagrams: index > 0,
  }));

  await act(async () => root.render(<FormatTopicAllocator
    formats={[{ key: "mcq", label: "MCQ", count: 6 }]}
    allocations={allocations}
    onChange={jest.fn()}
    bookIds={["book"]}
    details={{ book: { topic_catalog: [{ textbook_id: "book", topic_id: "topic-0", name: "Topic 0", parent_topic: "" }] } }}
    difficulty="medium"
    onExtract={jest.fn()}
    onBack={jest.fn()}
    onConfirm={jest.fn()}
  />));

  expect(container.textContent).toContain("5 diagrams selected — maximum reached");
  expect(container.querySelector(".diagram-toggle input").disabled).toBe(true);
  act(() => root.unmount());
  container.remove();
});

test("auto assign replaces manual rows with a complete allocation", async () => {
  global.IS_REACT_ACT_ENVIRONMENT = true;
  const container = document.createElement("div");
  document.body.appendChild(container);
  const root = createRoot(container);
  const onChange = jest.fn();
  await act(async () => root.render(<FormatTopicAllocator
    formats={[{ key: "mcq", label: "MCQ", count: 2 }]}
    allocations={[{ format: "mcq", textbook_id: "book", topic_id: "old", name: "Old", count: 2 }]}
    onChange={onChange}
    bookIds={["book"]}
    details={{ book: { topic_catalog: [
      { textbook_id: "book", topic_id: "one", name: "One", parent_topic: "" },
      { textbook_id: "book", topic_id: "two", name: "Two", parent_topic: "" },
    ] } }}
    difficulty="medium"
    onExtract={jest.fn()}
    onBack={jest.fn()}
    onConfirm={jest.fn()}
  />));
  await act(async () => container.querySelector('[data-testid="format-auto-assign"]').click());
  const rows = onChange.mock.calls.at(-1)[0];
  expect(rows.reduce((total, row) => total + row.count, 0)).toBe(2);
  expect(rows.some((row) => row.topic_id === "old")).toBe(false);
  act(() => root.unmount());
  container.remove();
});

test("auto assign remains unavailable until topics are extracted", async () => {
  global.IS_REACT_ACT_ENVIRONMENT = true;
  const container = document.createElement("div");
  document.body.appendChild(container);
  const root = createRoot(container);
  await act(async () => root.render(<FormatTopicAllocator
    formats={[{ key: "mcq", label: "MCQ", count: 1 }]}
    allocations={[]}
    onChange={jest.fn()}
    bookIds={["book"]}
    details={{ book: { topic_catalog: [] } }}
    difficulty="medium"
    onExtract={jest.fn()}
    onBack={jest.fn()}
    onConfirm={jest.fn()}
  />));
  expect(container.querySelector('[data-testid="format-auto-assign"]').disabled).toBe(true);
  expect(container.textContent).toContain("Extract topics from a selected textbook before auto assigning.");
  act(() => root.unmount());
  container.remove();
});
