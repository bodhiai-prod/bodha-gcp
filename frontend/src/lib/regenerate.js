// When the per-question "regenerate" button is usable, and which question the
// server currently says it is rewriting.
//
// The rules live here rather than inline in PaperView because they are the only
// branching part of the feature, and the repo's frontend tests are pure-logic
// (there is no component renderer in the toolchain).

// The question id the backend is regenerating right now, or null. `paper.regen`
// has the same shape as `paper.topup`.
export const regeneratingId = (paper) =>
  paper?.regen?.status === "generating" ? paper.regen.question_id : null;

// Every condition below mirrors a guard the endpoint enforces, so a disabled
// button and a rejected request never disagree. Edit mode is the exception the
// server cannot know about: `draft` holds unsaved local changes that the next
// save would write straight over a server-side rewrite.
export const canRegenerate = ({ paper, editing = false, regenId = null } = {}) => {
  if (!paper || editing) return false;
  if (paper.is_competitive) return false;
  if (paper.generation_status !== "ready") return false;
  if (paper.topup?.status === "generating") return false;
  if (regenId || regeneratingId(paper)) return false;
  return true;
};
