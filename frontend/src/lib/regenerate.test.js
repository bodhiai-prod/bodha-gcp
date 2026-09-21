import { canRegenerate, regeneratingId } from "./regenerate";

const ready = { generation_status: "ready" };

test("a ready school paper can regenerate a question", () => {
  expect(canRegenerate({ paper: ready })).toBe(true);
});

test("every server guard has a matching disabled button", () => {
  expect(canRegenerate({ paper: null })).toBe(false);
  expect(canRegenerate({ paper: { ...ready, is_competitive: true } })).toBe(false);
  expect(canRegenerate({ paper: { generation_status: "pending" } })).toBe(false);
  expect(canRegenerate({ paper: { generation_status: "failed" } })).toBe(false);
  expect(
    canRegenerate({ paper: { ...ready, topup: { status: "generating" } } })
  ).toBe(false);
  expect(
    canRegenerate({ paper: { ...ready, regen: { status: "generating", question_id: "q1" } } })
  ).toBe(false);
});

test("edit mode blocks it even though the server would allow it", () => {
  expect(canRegenerate({ paper: ready, editing: true })).toBe(false);
});

test("an in-flight click blocks a second one before the server has answered", () => {
  expect(canRegenerate({ paper: ready, regenId: "q1" })).toBe(false);
});

test("a finished or failed job leaves the button usable again", () => {
  expect(regeneratingId({ ...ready, regen: { status: "ready", question_id: "q1" } })).toBe(null);
  expect(canRegenerate({ paper: { ...ready, regen: { status: "failed", question_id: "q1" } } })).toBe(true);
});

test("regeneratingId names the question the spinner belongs to", () => {
  expect(regeneratingId({ regen: { status: "generating", question_id: "q7" } })).toBe("q7");
  expect(regeneratingId({})).toBe(null);
});
