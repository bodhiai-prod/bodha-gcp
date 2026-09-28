import { canonicalQuestionType, questionTypeLabel } from "./questionType";

test("normalizes legacy cognitive type aliases for display", () => {
  expect(canonicalQuestionType("problem")).toBe("concept");
  expect(canonicalQuestionType("Numerical")).toBe("application");
  expect(questionTypeLabel("concept")).toBe("Concept Based");
});
