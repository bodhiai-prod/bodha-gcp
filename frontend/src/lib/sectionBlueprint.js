export const allocationKey = (sectionId, groupId, textbookId, topicId) =>
  JSON.stringify([sectionId, groupId, textbookId, topicId]);

export function retainCompatibleAllocations(rows, previous, next, bookIds) {
  const groups = (bp) => new Map((bp?.sections || []).flatMap(s => s.groups.map(g =>
    [JSON.stringify([s.id, g.id]), JSON.stringify([g.count, g.marks, g.format, s.attempt_count, s.choice_set])])));
  const oldGroups = groups(previous), newGroups = groups(next);
  return rows.filter(row => {
    const key = JSON.stringify([row.section_id, row.group_id]);
    return bookIds.includes(row.textbook_id) && newGroups.has(key) && oldGroups.get(key) === newGroups.get(key);
  });
}

export function updateAllocation(rows, sectionId, groupId, topic, patch) {
  const key = allocationKey(sectionId, groupId, topic.textbook_id, topic.topic_id);
  const matches = row => allocationKey(row.section_id, row.group_id, row.textbook_id, row.topic_id) === key;
  if (patch === null) return rows.filter(row => !matches(row));
  const existing = rows.find(matches);
  const next = { section_id: sectionId, group_id: groupId, textbook_id: topic.textbook_id,
    topic_id: topic.topic_id, count: 1, difficulty: null, diagrams: false, ...existing, ...patch };
  return existing ? rows.map(row => matches(row) ? next : row) : [...rows, next];
}
