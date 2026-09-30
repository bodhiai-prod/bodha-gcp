import React, { act } from "react";
import { createRoot } from "react-dom/client";
import PaperView from "./PaperView";
import { api } from "@/lib/api";
import { toast } from "sonner";

jest.mock("@/lib/api", () => ({ api: { get: jest.fn(), post: jest.fn() } }));
jest.mock("react-router-dom", () => ({ useParams: () => ({ id: "paper" }), Link: ({ children }) => <a>{children}</a> }));
jest.mock("@/components/Header", () => () => null);
jest.mock("@/components/PaperSummary", () => () => null);
jest.mock("@/components/MathText", () => ({ text }) => <span>{text}</span>);
jest.mock("@/context/ConfirmationContext", () => ({ useConfirmation: () => jest.fn() }));
jest.mock("sonner", () => ({ toast: { success: jest.fn(), info: jest.fn(), error: jest.fn() } }));
jest.mock("@/components/ui/dropdown-menu", () => ({
  DropdownMenu: ({ children }) => <div>{children}</div>,
  DropdownMenuContent: () => null,
  DropdownMenuItem: () => null,
  DropdownMenuTrigger: ({ children }) => <div>{children}</div>,
}));
let container, root;
beforeEach(() => {
  global.IS_REACT_ACT_ENVIRONMENT = true;
  container = document.createElement("div");
  document.body.appendChild(container);
  root = createRoot(container);
});
afterEach(() => { act(() => root.unmount()); container.remove(); jest.clearAllMocks(); });
const paper = (extra = {}) => ({
  id: "paper", title: "Maths draft", generation_status: "needs_review", target_question_count: 41,
  quality_review: { status: "needs_review" },
  sections: [{ section_id: "A", title: "Section A", questions: Array.from({ length: 38 }, (_, i) => ({
    id: `q${i}`, question: `Explain algebra operation ${i}.`, marks: 1, format: "short_answer", type: "concept",
  })) }], ...extra,
});
const render = async (value) => {
  api.get.mockImplementation(url => Promise.resolve({ data: url === "/branding-presets" ? [] : value }));
  await act(async () => root.render(<PaperView />));
};
test("legacy near-complete paper is visible and generate pending queues continuation", async () => {
  await render(paper());
  expect(container.querySelector('[data-testid="paper-view-page"]')).not.toBeNull();
  expect(container.textContent).toContain("Explain algebra operation 37.");
  const button = container.querySelector('[data-testid="generate-missing-questions-button"]');
  expect(button.textContent).toContain("Generate pending");
  api.post.mockResolvedValue({ data: { status: "queued", revision: 2 } });
  await act(async () => button.click());
  expect(api.post).toHaveBeenCalledWith("/papers/paper/generate-missing", { count: null });
  expect(toast.success).toHaveBeenCalledWith(expect.not.stringContaining("undefined"));
});
test("queued continuation keeps saved paper on screen", async () => {
  await render(paper({ generation_status: "queued", completion: { status: "queued", ready: false }, quality_review: { status: "running" } }));
  expect(container.querySelector('[data-testid="paper-view-page"]')).not.toBeNull();
  expect(container.textContent).toContain("Explain algebra operation 37.");
});
test("OR-only shortfall still offers generate pending", async () => {
  await render(paper({ target_question_count: 38, completion: { status: "incomplete", ready: false, unresolved_count: 10 } }));
  expect(container.querySelector('[data-testid="generate-missing-questions-button"]')).not.toBeNull();
});
