export const allocationKey = (sectionId, groupId, slotId, textbookId, topicId) =>
  JSON.stringify([sectionId, groupId, slotId || null, textbookId, topicId]);

export function groupSlots(group) {
  if (group?.slots?.length) return group.slots;
  return Array.from({ length: group?.count || 0 }, (_, index) => ({
    id: `${group.id}-${index + 1}`,
    marks: group.marks,
    format: group.format,
    parent_id: `${group.id}-${index + 1}`,
    parent_label: "",
    part_label: "",
  }));
}

export function retainCompatibleAllocations(rows, previous, next, bookIds) {
  const groups = (bp) => new Map((bp?.sections || []).flatMap(s => s.groups.flatMap(g => g.slots?.length
    ? groupSlots(g).map(slot => [JSON.stringify([s.id, g.id, slot.id]), JSON.stringify([slot.marks, slot.format, slot.parent_id, s.attempt_count, s.choice_set])])
    : [[JSON.stringify([s.id, g.id, null]), JSON.stringify([g.count, g.marks, g.format, s.attempt_count, s.choice_set])]])));
  const oldGroups = groups(previous), newGroups = groups(next);
  return rows.filter(row => {
    const key = JSON.stringify([row.section_id, row.group_id, row.slot_id || null]);
    return bookIds.includes(row.textbook_id) && newGroups.has(key) && oldGroups.get(key) === newGroups.get(key);
  });
}

export function updateAllocation(rows, sectionId, groupId, slotOrTopic, topicOrPatch, maybePatch) {
  // Keep the public helper compatible with saved/older callers that passed
  // (rows, section, group, topic, patch) before slot IDs existed.
  const hasSlot = typeof slotOrTopic === "string" || slotOrTopic === null;
  const slotId = hasSlot ? slotOrTopic : null;
  const topic = hasSlot ? topicOrPatch : slotOrTopic;
  const patch = hasSlot ? maybePatch : topicOrPatch;
  const key = allocationKey(sectionId, groupId, slotId, topic.textbook_id, topic.topic_id);
  const matches = row => allocationKey(row.section_id, row.group_id, row.slot_id, row.textbook_id, row.topic_id) === key;
  if (patch === null) return rows.filter(row => !matches(row));
  const existing = rows.find(matches);
  const next = { section_id: sectionId, group_id: groupId, slot_id: slotId || null, textbook_id: topic.textbook_id,
    topic_id: topic.topic_id, count: 1, difficulty: null, diagrams: false, ...existing, ...patch };
  return existing ? rows.map(row => matches(row) ? next : row) : [...rows, next];
}
