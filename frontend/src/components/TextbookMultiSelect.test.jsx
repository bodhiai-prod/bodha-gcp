import React, { act, useState } from "react";
import { createRoot } from "react-dom/client";
import TextbookMultiSelect from "./TextbookMultiSelect";

jest.mock("@/components/ui/popover", () => ({
  Popover: ({ children }) => <div>{children}</div>,
  PopoverTrigger: ({ children }) => children,
  PopoverContent: ({ children, ...props }) => <div {...props}>{children}</div>,
}));
jest.mock("@/components/ui/checkbox", () => ({
  Checkbox: ({ checked, ...props }) => <span data-checked={checked} {...props} />,
}));
jest.mock("@/components/ui/command", () => ({
  Command: ({ children }) => <div>{children}</div>,
  CommandEmpty: ({ children }) => <div>{children}</div>,
  CommandInput: ({ value, onValueChange, ...props }) => <input value={value} onChange={(event) => onValueChange(event.target.value)} {...props} />,
  CommandItem: ({ children, onSelect, ...props }) => <button type="button" onClick={() => onSelect()} {...props}>{children}</button>,
  CommandList: ({ children, ...props }) => <div {...props}>{children}</div>,
}));

const books = [
  { id: "physics", original_filename: "Physics Star Batch Advanced Assignments.pdf", subject: "Physics", class_name: "12", status: "indexed", topic_count: 14 },
  { id: "chemistry", original_filename: "Chemistry Foundations.pdf", subject: "Chemistry", class_name: "11", status: "ingesting", topic_count: 0 },
];

let container;
let root;

function Harness() {
  const [selectedIds, setSelectedIds] = useState([]);
  return <TextbookMultiSelect textbooks={books} selectedIds={selectedIds} onChange={setSelectedIds} />;
}

const setNativeValue = (input, value) => {
  Object.getOwnPropertyDescriptor(HTMLInputElement.prototype, "value").set.call(input, value);
  input.dispatchEvent(new Event("input", { bubbles: true }));
};

beforeEach(() => {
  global.IS_REACT_ACT_ENVIRONMENT = true;
  container = document.createElement("div");
  document.body.appendChild(container);
  root = createRoot(container);
});

afterEach(() => {
  act(() => root.unmount());
  container.remove();
  document.body.innerHTML = "";
});

test("searches books and keeps selected books when the search changes", async () => {
  await act(async () => root.render(<Harness />));
  const trigger = container.querySelector('[data-testid="paper-textbook-select"]');
  expect(trigger.textContent).toContain("Select textbooks");

  await act(async () => trigger.click());
  const physics = document.body.querySelector('[data-testid="textbook-picker-option-physics"]');
  await act(async () => physics.click());
  expect(container.querySelector('[data-testid="textbook-chip-physics"]')).not.toBeNull();
  expect(trigger.textContent).toContain("1 textbook selected");

  const search = document.body.querySelector('[data-testid="textbook-search-input"]');
  await act(async () => setNativeValue(search, "chemistry"));
  expect(document.body.querySelector('[data-testid="textbook-picker-option-physics"]')).toBeNull();
  expect(document.body.querySelector('[data-testid="textbook-picker-option-chemistry"]')).not.toBeNull();
  expect(container.querySelector('[data-testid="textbook-chip-physics"]')).not.toBeNull();
});

test("selects multiple books and exposes an accessible remove action", async () => {
  await act(async () => root.render(<Harness />));
  await act(async () => container.querySelector('[data-testid="paper-textbook-select"]').click());
  await act(async () => document.body.querySelector('[data-testid="textbook-picker-option-physics"]').click());
  await act(async () => document.body.querySelector('[data-testid="textbook-picker-option-chemistry"]').click());

  expect(container.querySelector('[data-testid="textbook-chip-physics"]')).not.toBeNull();
  expect(container.querySelector('[data-testid="textbook-chip-chemistry"]')).not.toBeNull();
  const remove = container.querySelector('[data-testid="textbook-chip-remove-physics"]');
  expect(remove.getAttribute("aria-label")).toBe("Remove Physics Star Batch Advanced Assignments.pdf");
  await act(async () => remove.click());
  expect(container.querySelector('[data-testid="textbook-chip-physics"]')).toBeNull();
  expect(container.querySelector('[data-testid="textbook-chip-chemistry"]')).not.toBeNull();
});
