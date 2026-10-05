import React, { act } from "react";
import { createRoot } from "react-dom/client";
import QBank from "./QBank";
import { api } from "@/lib/api";

jest.mock("@/lib/api", () => ({ api: { get: jest.fn(), delete: jest.fn(), post: jest.fn() } }));
jest.mock("@/components/Header", () => () => null);
jest.mock("@/context/ConfirmationContext", () => ({ useConfirmation: () => jest.fn() }));
jest.mock("sonner", () => ({ toast: { success: jest.fn(), error: jest.fn() } }));
jest.mock("@/components/MathText", () => ({ text, className }) => (
  <div data-testid="rendered-question" className={className}>{text}</div>
));

let container;
let root;

beforeEach(() => {
  global.IS_REACT_ACT_ENVIRONMENT = true;
  container = document.createElement("div");
  document.body.appendChild(container);
  root = createRoot(container);
  api.get.mockResolvedValue({ data: [{
    id: "question-1",
    question: "Solve $x^2 = 4$.",
    type: "concept",
    difficulty: "easy",
    marks: 2,
  }] });
});

afterEach(() => {
  act(() => root.unmount());
  container.remove();
  jest.clearAllMocks();
});

test("renders saved question content through MathText", async () => {
  await act(async () => root.render(<QBank />));

  expect(container.querySelector('[data-testid="rendered-question"]').textContent)
    .toBe("Solve $x^2 = 4$.");
});
