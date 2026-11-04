import React, { act } from "react";
import { createRoot } from "react-dom/client";
import GeneratedPapers from "./GeneratedPapers";
import { api } from "@/lib/api";

jest.mock("@/lib/api", () => ({ api: { get: jest.fn(), delete: jest.fn() } }));
jest.mock("@/components/Header", () => () => null);
jest.mock("@/context/ConfirmationContext", () => ({ useConfirmation: () => jest.fn() }));
jest.mock("sonner", () => ({ toast: { success: jest.fn(), error: jest.fn() } }));
jest.mock("react-router-dom", () => ({
  Link: ({ to, children, ...props }) => <a href={to} {...props}>{children}</a>,
}));

let container;
let root;

const papers = Array.from({ length: 18 }, (_, index) => ({
  id: `paper-${index + 1}`,
  title: `Paper ${index + 1}`,
  subject: "Physics",
  class_name: "12",
  total_marks: 50,
  duration_minutes: 60,
  created_at: new Date(2026, 0, index + 1).toISOString(),
  library_status: { key: "ready", label: "Ready", active: false },
}));

beforeEach(() => {
  global.IS_REACT_ACT_ENVIRONMENT = true;
  container = document.createElement("div");
  document.body.appendChild(container);
  root = createRoot(container);
  api.get.mockResolvedValue({ data: papers });
});

afterEach(() => {
  act(() => root.unmount());
  container.remove();
  jest.clearAllMocks();
});

test("shows the latest 15 papers first and lets the teacher page through the remainder", async () => {
  await act(async () => root.render(<GeneratedPapers />));

  expect(container.querySelectorAll('[data-testid^="generated-paper-row-"]')).toHaveLength(15);
  expect(container.textContent).toContain("Paper 18");
  expect(container.textContent).not.toContain("Paper 3");
  expect(container.querySelector('[data-testid="generated-papers-count"]').textContent).toContain("Showing 1–15 of 18 papers");
  expect(container.querySelector('[data-testid="generated-papers-total"]').textContent).toBe("18 papers total");

  await act(async () => container.querySelector('[data-testid="generated-papers-next"]').click());

  expect(container.querySelectorAll('[data-testid^="generated-paper-row-"]')).toHaveLength(3);
  expect(container.textContent).toContain("Paper 3");
  expect(container.querySelector('[data-testid="generated-papers-count"]').textContent).toContain("Showing 16–18 of 18 papers");
  expect(container.querySelector('[data-testid="generated-papers-total"]').textContent).toBe("18 papers total");
});

test("keeps the total while the result summary reflects filters", async () => {
  api.get.mockResolvedValue({ data: papers.map((paper, index) => index === 17 ? { ...paper, subject: "Maths" } : paper) });
  await act(async () => root.render(<GeneratedPapers />));
  const subjectFilter = container.querySelector('[data-testid="generated-papers-subject-filter"]');
  await act(async () => {
    subjectFilter.value = "Maths";
    subjectFilter.dispatchEvent(new Event("change", { bubbles: true }));
  });

  expect(container.querySelector('[data-testid="generated-papers-total"]').textContent).toBe("18 papers total");
  expect(container.querySelector('[data-testid="generated-papers-count"]').textContent).toContain("of 1 paper (18 total)");
});
