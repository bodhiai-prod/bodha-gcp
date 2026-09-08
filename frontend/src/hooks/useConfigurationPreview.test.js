import React, { act } from "react";
import { createRoot } from "react-dom/client";
import { api } from "../lib/api";
import useConfigurationPreview from "./useConfigurationPreview";

jest.mock("../lib/api", () => ({ api: { post: jest.fn() } }));
let container, root;
function Harness({ request }) {
  const state = useConfigurationPreview(JSON.stringify(request));
  return <pre>{JSON.stringify(state)}</pre>;
}
beforeEach(() => {
  jest.useFakeTimers();
  global.IS_REACT_ACT_ENVIRONMENT = true;
  container = document.createElement("div");
  document.body.appendChild(container);
  root = createRoot(container);
});
afterEach(() => { act(() => root.unmount()); container.remove(); jest.useRealTimers(); jest.clearAllMocks(); });
test("debounces changes and ignores a stale backend response", async () => {
  const resolvers = [];
  api.post.mockImplementation(() => new Promise(resolve => resolvers.push(resolve)));
  await act(async () => { root.render(<Harness request={{ total_marks: 10 }} />); });
  await act(async () => { jest.advanceTimersByTime(650); });
  await act(async () => { root.render(<Harness request={{ total_marks: 20 }} />); });
  expect(api.post.mock.calls[0][2].signal.aborted).toBe(true);
  await act(async () => { jest.advanceTimersByTime(650); });
  await act(async () => { resolvers[1]({ data: { valid: true, summary: { question_count: 20 } } }); });
  await act(async () => { resolvers[0]({ data: { valid: true, summary: { question_count: 10 } } }); });
  const state = JSON.parse(container.textContent);
  expect(state.preview.summary.question_count).toBe(20);
  expect(state.loading).toBe(false);
});
test("failed previews replace prior values with a visible error", async () => {
  api.post.mockRejectedValue({ response: { data: { detail: "Invalid allocation" } } });
  await act(async () => { root.render(<Harness request={{ total_marks: 10 }} />); });
  await act(async () => { jest.advanceTimersByTime(650); });
  expect(JSON.parse(container.textContent)).toEqual({ preview: null, loading: false, error: "Invalid allocation" });
});
