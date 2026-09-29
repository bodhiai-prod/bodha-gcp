// Internal-OR recovery is separate from missing-question top-up: a complete
// paper may have every numbered block while still omitting student-facing ORs.
export const internalChoiceRecovery = (paper) => {
  const recovery = paper?.generation_recovery;
  return recovery?.reason === "internal_choices" && recovery.status !== "ready"
    ? recovery
    : null;
};

export const internalChoiceRepairLive = (paper) =>
  ["queued", "recovering"].includes(internalChoiceRecovery(paper)?.status);

export const canRepairInternalChoices = ({ paper, editing = false } = {}) => {
  if (!internalChoiceRecovery(paper) || editing) return false;
  if (paper?.generation_status !== "ready") return false;
  if (internalChoiceRepairLive(paper)) return false;
  if (paper?.topup?.status === "generating" || paper?.regen?.status === "generating") return false;
  return true;
};
