import React, { act } from "react";
import { createRoot } from "react-dom/client";
import QuestionCountStepper from "./QuestionCountStepper";

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

test("accepts a typed question count and clamps it to the allocation capacity", async () => {
  const onChange = jest.fn();
  await act(async () => root.render(<QuestionCountStepper value={2} max={5} onChange={onChange} label="questions for Algebra" />));
  const input = container.querySelector('input[type="number"]');
  const setNativeValue = Object.getOwnPropertyDescriptor(HTMLInputElement.prototype, "value").set;

  await act(async () => {
    setNativeValue.call(input, "4");
    input.dispatchEvent(new Event("input", { bubbles: true }));
  });
  await act(async () => {
    input.dispatchEvent(new FocusEvent("focusout", { bubbles: true }));
  });
  expect(onChange).toHaveBeenLastCalledWith(4);

  await act(async () => {
    setNativeValue.call(input, "12");
    input.dispatchEvent(new Event("input", { bubbles: true }));
  });
  await act(async () => {
    input.dispatchEvent(new FocusEvent("focusout", { bubbles: true }));
  });
  expect(onChange).toHaveBeenLastCalledWith(5);
});

test("keeps the existing minus and plus controls", async () => {
  const onChange = jest.fn();
  await act(async () => root.render(<QuestionCountStepper value={2} max={3} onChange={onChange} label="questions for Algebra" />));
  const [decrease, increase] = container.querySelectorAll("button");
  await act(async () => decrease.click());
  expect(onChange).toHaveBeenCalledWith(1);
  await act(async () => increase.click());
  expect(onChange).toHaveBeenCalledWith(3);
});
