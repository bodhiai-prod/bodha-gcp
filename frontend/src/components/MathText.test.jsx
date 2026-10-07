import React, { act } from "react";
import { createRoot } from "react-dom/client";
import MathText from "./MathText";

jest.mock("react-katex", () => ({
  InlineMath: ({ math }) => <span data-testid="inline-math">{math}</span>,
  BlockMath: ({ math }) => <div data-testid="block-math">{math}</div>,
}));

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
});

test("adds the responsive math root to inline equations", async () => {
  await act(async () => root.render(<MathText className="answer-math" text="$a + b + c + d = e$" />));

  const rootElement = container.querySelector(".math-text");
  expect(rootElement).not.toBeNull();
  expect(rootElement.classList.contains("answer-math")).toBe(true);
  expect(container.querySelector('[data-testid="inline-math"]').textContent).toBe("a + b + c + d = e");
});

test("adds the responsive math root to block equations", async () => {
  await act(async () => root.render(<MathText text="$$a + b + c + d = e$$" />));

  expect(container.querySelector(".math-text")).not.toBeNull();
  expect(container.querySelector('[data-testid="block-math"]').textContent).toBe("a + b + c + d = e");
});
