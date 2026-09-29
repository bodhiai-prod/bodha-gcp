import {
  canRepairInternalChoices,
  internalChoiceRecovery,
  internalChoiceRepairLive,
} from "./internalChoiceRepair";

const failed = {
  generation_status: "ready",
  generation_recovery: { status: "needs_review", reason: "internal_choices" },
};

test("identifies only unfinished internal-choice recovery", () => {
  expect(internalChoiceRecovery(failed)).toEqual(failed.generation_recovery);
  expect(internalChoiceRecovery({ generation_recovery: { status: "ready", reason: "internal_choices" } })).toBe(null);
  expect(internalChoiceRecovery({ generation_recovery: { status: "needs_review", reason: "allocation_metadata" } })).toBe(null);
});

test("allows Resolve after automatic repair fails", () => {
  expect(canRepairInternalChoices({ paper: failed })).toBe(true);
  expect(canRepairInternalChoices({ paper: failed, editing: true })).toBe(false);
  expect(canRepairInternalChoices({ paper: { ...failed, topup: { status: "generating" } } })).toBe(false);
});

test("polling state disables Resolve while repair is active", () => {
  const active = { ...failed, generation_recovery: { status: "recovering", reason: "internal_choices" } };
  expect(internalChoiceRepairLive(active)).toBe(true);
  expect(canRepairInternalChoices({ paper: active })).toBe(false);
});
