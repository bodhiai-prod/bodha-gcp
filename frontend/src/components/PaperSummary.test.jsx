import React, { act } from "react";
import { createRoot } from "react-dom/client";
import PaperSummary from "./PaperSummary";

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

test("marks long textbook filenames as wrap-safe", async () => {
  const filename = "CHEMISTRY_STAR_BATCH_JEE_ADVANCED_ASSIGNMENTS.pdf";
  const paper = {
    source_textbooks: [filename],
    summary: {
      offered_marks: 100,
      target_marks: 100,
      question_count: 10,
      target_question_count: 10,
      duration_minutes: 180,
      printed_marks: 100,
      sections: [],
      cognitive: [],
      difficulty: [],
      diagrams: { requested: 0, ready: 0, pending: 0, failed: 0 },
      internal_choices: 0,
      mismatches: [],
      status: "ready",
    },
  };

  await act(async () => root.render(<PaperSummary paper={paper} editing={false} />));

  const book = container.querySelector(".paper-summary-book");
  expect(book).not.toBeNull();
  expect(book.textContent).toBe(filename);
});
