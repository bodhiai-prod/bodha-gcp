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
  expect(button.textContent).toContain("Finish remaining items");
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

test("ignoring a stopped warning hides the banner and enables downloads", async () => {
  const draft = paper({
    generation_status: "cancelled",
    completion: { status: "cancelled", ready: false, export_eligible: false },
    quality_review: { status: "cancelled" },
  });
  await render(draft);
  const ignore = container.querySelector('[data-testid="ignore-export-warning-button"]');
  expect(ignore).not.toBeNull();
  expect(container.querySelector('[data-testid="download-menu-button"]').disabled).toBe(true);
  api.post.mockResolvedValue({ data: {
    ...draft,
    export_warning_acknowledgement: { ignored: true, acknowledged_by: "teacher" },
    presentation: { ready: true, stage: "Ready" },
  } });
  await act(async () => ignore.click());
  expect(api.post).toHaveBeenCalledWith("/papers/paper/ignore-export-warning");
  expect(container.querySelector('[data-testid="partial-banner"]')).toBeNull();
  expect(container.querySelector('[data-testid="download-menu-button"]').disabled).toBe(false);
  expect(toast.success).toHaveBeenCalledWith(expect.stringContaining("downloads are now available"));
});

test("checking preview has one curated panel and no raw diagnostics", async () => {
  await render(paper({ generation_status: "ready",
    quality_review: { status: "running", message: "{'slot_id': 'secret-slot'}" },
    completion: { status: "running", ready: false, export_eligible: false, actual_scored_parts: 38,
      target_scored_parts: 41, pending_alternatives: 10, actual_printed_questions: 19, target_printed_questions: 22 },
    instruction_check: { honoured: false, misses: ["secret-slot"] },
  }));
  expect(container.textContent).toContain("Checking and completing");
  expect(container.textContent).toContain("10 alternatives pending");
  expect(container.textContent).toContain("Explain algebra operation 37.");
  expect(container.textContent).not.toContain("secret-slot");
  expect(container.textContent).not.toContain("% complete");
  expect(container.querySelectorAll('[data-testid="paper-quality-running-banner"]')).toHaveLength(1);
  expect(container.querySelector('[data-testid="partial-banner"]')).toBeNull();
});

test("ambiguous placement opens review instead of automatic repair", async () => {
  await render(paper({ completion: { status: "incomplete", ready: false },
    presentation: { action: "review_placement", action_label: "Review question placement", message: "Review the saved questions." },
  }));
  const button = container.querySelector('[data-testid="generate-missing-questions-button"]');
  expect(button.textContent).toContain("Review question placement");
  await act(async () => button.click());
  expect(container.querySelector('[data-testid="edit-question-q0"]')).not.toBeNull();
  expect(api.post).not.toHaveBeenCalled();
});

test("older polling response cannot overwrite a newer completed revision", async () => {
  jest.useFakeTimers();
  try {
    await render(paper({ generation_status: "generating", generation_revision: 1 }));
    const waiting = [];
    api.get.mockImplementation(url => url === "/branding-presets" ? Promise.resolve({ data: [] }) : new Promise(resolve => waiting.push(resolve)));
    await act(async () => jest.advanceTimersByTime(3000));
    expect(waiting.length).toBeGreaterThanOrEqual(2);
    await act(async () => waiting[waiting.length - 1]({ data: paper({ title: "Completed revision", generation_revision: 2,
      generation_status: "ready", target_question_count: 38, quality_review: { status: "passed" },
      completion: { status: "ready", ready: true, export_eligible: true } }) }));
    await act(async () => waiting[0]({ data: paper({ title: "Stale revision", generation_revision: 1 }) }));
    expect(container.textContent).toContain("Completed revision");
    expect(container.textContent).not.toContain("Stale revision");
  } finally { jest.useRealTimers(); }
});
