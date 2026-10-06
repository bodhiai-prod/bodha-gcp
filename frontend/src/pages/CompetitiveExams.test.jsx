import React, { act } from "react";
import { createRoot } from "react-dom/client";
import CompetitiveExams from "./CompetitiveExams";
import { api } from "@/lib/api";

jest.mock("@/lib/api", () => ({ api: { get: jest.fn() } }));
jest.mock("@/components/Header", () => () => <header data-testid="header" />);

let container;
let root;

beforeEach(() => {
  global.IS_REACT_ACT_ENVIRONMENT = true;
  container = document.createElement("div");
  document.body.appendChild(container);
  root = createRoot(container);
});

afterEach(() => {
  act(() => root.unmount());
  container.remove();
  jest.clearAllMocks();
});

test("locks competitive-page creation controls and shows the in-progress message", async () => {
  api.get.mockResolvedValue({ data: [] });
  await act(async () => root.render(<CompetitiveExams />));

  const status = [...container.querySelectorAll("p")].find((element) => element.textContent === "in progress, stay tuned");
  expect(status).not.toBeNull();
  expect(status.className).toContain("font-bold");
  expect(status.className).toContain("text-red-700");
  expect(container.textContent).toContain("Create your first test");
  expect([...container.querySelectorAll('[data-testid^="preset-"]')]).toHaveLength(5);
  expect([...container.querySelectorAll('[data-testid^="preset-"]')].every((button) => button.disabled)).toBe(true);
  expect(container.querySelector('[data-testid="new-exam-name"]').disabled).toBe(true);
  expect(container.querySelector('[data-testid="new-exam-description"]').disabled).toBe(true);
  expect(container.querySelector('[data-testid="new-exam-type"]').disabled).toBe(true);
  expect(container.querySelector('[data-testid="new-exam-submit"]').disabled).toBe(true);
});

test("renders existing libraries as static cards rather than detail links", async () => {
  api.get.mockResolvedValue({ data: [{ id: "exam-1", name: "JEE", papers_count: 2, questions_count: 20 }] });
  await act(async () => root.render(<CompetitiveExams />));

  expect(container.querySelector('[data-testid="exam-card-exam-1"]')).not.toBeNull();
  expect(container.querySelector('[data-testid="exam-card-exam-1"]').tagName).toBe("ARTICLE");
  expect(container.querySelector('[data-testid="exam-card-exam-1"] a')).toBeNull();
});
