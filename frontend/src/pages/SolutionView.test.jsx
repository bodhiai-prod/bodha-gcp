import React, { act } from "react";
import { createRoot } from "react-dom/client";
import SolutionView from "./SolutionView";
import { api } from "@/lib/api";
import { printDocument } from "@/lib/print";

jest.mock("@/lib/api", () => ({ api: { get: jest.fn(), post: jest.fn(), delete: jest.fn(), patch: jest.fn() } }));
jest.mock("@/lib/print", () => ({ printDocument: jest.fn(), diagramsReady: () => true }));
jest.mock("react-router-dom", () => ({ useParams: () => ({ id: "paper" }), Link: ({ children, to, ...props }) => <a href={to} {...props}>{children}</a> }));
jest.mock("@/components/Header", () => () => null);
jest.mock("@/components/PaperSummary", () => () => <aside data-testid="paper-summary" />);
jest.mock("@/components/MathText", () => ({ text }) => <span>{text}</span>);
jest.mock("@/context/ConfirmationContext", () => ({ useConfirmation: () => jest.fn() }));
jest.mock("sonner", () => ({ toast: { success: jest.fn(), info: jest.fn(), error: jest.fn() } }));
jest.mock("@/components/ui/dropdown-menu", () => ({
  DropdownMenu: ({ children }) => <div>{children}</div>,
  DropdownMenuContent: ({ children }) => <div>{children}</div>,
  DropdownMenuItem: ({ children, onSelect, ...props }) => <button onClick={onSelect} {...props}>{children}</button>,
  DropdownMenuTrigger: ({ children }) => <div>{children}</div>,
}));

const paper = (extra = {}) => ({
  id: "paper", title: "Biology test", class_name: "12", subject: "Biology", total_marks: 3,
  sections: [{ title: "Section A", questions: [{ id: "q1", question: "Question one?", marks: 3, type: "concept", difficulty: "hard" }] }],
  solution: { status: "ready", sections: [{ answers: [{ question_id: "q1", answer: "Answer one." }] }] },
  ...extra,
});

let container, root;
beforeEach(() => {
  global.IS_REACT_ACT_ENVIRONMENT = true;
  container = document.createElement("div");
  document.body.appendChild(container);
  root = createRoot(container);
});
afterEach(() => { act(() => root.unmount()); container.remove(); jest.clearAllMocks(); });

test("solution keeps the paper workspace and applies or removes branding", async () => {
  const original = paper();
  const branded = paper({ branding_id: "preset-1", branding_name: "School", school_name: "School" });
  api.get.mockImplementation(url => Promise.resolve({ data: url === "/branding-presets"
    ? [{ id: "preset-1", name: "School" }] : original }));
  await act(async () => root.render(<SolutionView />));

  expect(container.querySelector('[data-testid="solution-view-page"]').className).toContain("max-w-7xl");
  expect(container.querySelector(".paper-with-summary [data-testid='solution-body']")).not.toBeNull();
  expect(container.querySelector(".paper-with-summary [data-testid='paper-summary']")).not.toBeNull();
  expect(container.querySelector('[data-testid="solution-body"] h1').textContent).toBe("Biology test");
  const select = container.querySelector('[data-testid="paper-branding-select"]');
  api.post.mockResolvedValue({ data: branded });
  await act(async () => {
    select.value = "preset-1";
    select.dispatchEvent(new Event("change", { bubbles: true }));
  });
  expect(api.post).toHaveBeenCalledWith("/papers/paper/branding/apply", { branding_id: "preset-1" });
  expect(select.value).toBe("preset-1");
  expect(container.querySelector('[data-testid="solution-body"]').textContent).toContain("School");

  api.delete.mockResolvedValue({ data: original });
  await act(async () => {
    select.value = "";
    select.dispatchEvent(new Event("change", { bubbles: true }));
  });
  expect(api.delete).toHaveBeenCalledWith("/papers/paper/branding");
  expect(select.value).toBe("");
});

test("keeps a saved branding selection when its preset is absent and uses existing export actions", async () => {
  const saved = paper({ branding_id: "deleted-preset", branding_name: "Saved school", school_name: "Saved school" });
  api.get.mockImplementation(url => Promise.resolve({ data: url === "/branding-presets" ? [] : saved }));
  await act(async () => root.render(<SolutionView />));

  const select = container.querySelector('[data-testid="paper-branding-select"]');
  expect(select.value).toBe("deleted-preset");
  expect(select.selectedOptions[0].textContent).toBe("Saved school");
  await act(async () => container.querySelector('[data-testid="download-solution-pdf-button"]').click());
  expect(printDocument).toHaveBeenCalledWith("Biology test - Solution");

  URL.createObjectURL = jest.fn(() => "blob:solution");
  URL.revokeObjectURL = jest.fn();
  const click = jest.spyOn(HTMLAnchorElement.prototype, "click").mockImplementation(() => {});
  api.get.mockResolvedValueOnce({ data: new Blob(["docx"]) });
  await act(async () => container.querySelector('[data-testid="download-solution-docx-button"]').click());
  expect(api.get).toHaveBeenCalledWith("/papers/paper/solution/docx", { responseType: "blob" });
  click.mockRestore();
  delete URL.createObjectURL;
  delete URL.revokeObjectURL;
});
