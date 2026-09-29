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

// The editor groups only adjacent slots that have identical generation
// requirements.  The server uses the same identifier and expands each topic
// count back into these individual slots before asking the model to generate.
export function allocationBuckets(group) {
  return groupSlots(group).reduce((buckets, slot) => {
    const marks = slot.marks ?? group.marks;
    const format = slot.format ?? group.format;
    const previous = buckets.at(-1);
    if (previous && previous.marks === marks && previous.format === format) {
      previous.slots.push(slot);
      return buckets;
    }
    buckets.push({
      id: `${group.id}:${slot.id}`,
      marks,
      format,
      slots: [slot],
    });
    return buckets;
  }, []);
}

export function normalizeBucketAllocations(rows, blueprint) {
  const slotBuckets = new Map();
  const groupBuckets = new Map();
  (blueprint?.sections || []).forEach(section => section.groups.forEach(group => {
    const buckets = allocationBuckets(group);
    groupBuckets.set(`${section.id}:${group.id}`, buckets);
    buckets.forEach(bucket => bucket.slots.forEach(slot => {
      slotBuckets.set(`${section.id}:${group.id}:${slot.id}`, bucket.id);
    }));
  }));
  const merged = new Map();
  rows.forEach(row => {
    const groupKey = `${row.section_id}:${row.group_id}`;
    const bucketId = row.bucket_id || slotBuckets.get(`${groupKey}:${row.slot_id}`)
      || (groupBuckets.get(groupKey)?.length === 1 ? groupBuckets.get(groupKey)[0].id : null);
    // A stale row is left untouched for server-side validation rather than
    // silently discarded from a saved configuration.
    if (!bucketId) {
      merged.set(`stale:${JSON.stringify(row)}`, row);
      return;
    }
    const key = JSON.stringify([row.section_id, row.group_id, bucketId, row.textbook_id, row.topic_id, row.difficulty || null, Boolean(row.diagrams)]);
    const previous = merged.get(key);
    merged.set(key, {
      ...row,
      slot_id: null,
      bucket_id: bucketId,
      count: (previous?.count || 0) + row.count,
    });
  });
  return [...merged.values()];
}

export function retainCompatibleAllocations(rows, previous, next, bookIds) {
  const buckets = (bp) => {
    const targets = new Map(), bySlot = new Map(), byGroup = new Map();
    (bp?.sections || []).forEach(section => section.groups.forEach(group => {
      const compatible = allocationBuckets(group);
      byGroup.set(JSON.stringify([section.id, group.id]), compatible);
      compatible.forEach(bucket => {
        const signature = JSON.stringify([bucket.slots.map(slot => slot.id), bucket.marks, bucket.format, section.attempt_count, section.choice_set]);
        targets.set(JSON.stringify([section.id, group.id, bucket.id]), signature);
        bucket.slots.forEach(slot => bySlot.set(JSON.stringify([section.id, group.id, slot.id]), bucket.id));
      });
    }));
    return { targets, bySlot, byGroup };
  };
  const oldBuckets = buckets(previous), newBuckets = buckets(next);
  return rows.filter(row => {
    const groupKey = JSON.stringify([row.section_id, row.group_id]);
    const bucketId = row.bucket_id || oldBuckets.bySlot.get(JSON.stringify([row.section_id, row.group_id, row.slot_id]))
      || (oldBuckets.byGroup.get(groupKey)?.length === 1 ? oldBuckets.byGroup.get(groupKey)[0].id : null);
    const key = JSON.stringify([row.section_id, row.group_id, bucketId]);
    return bookIds.includes(row.textbook_id) && oldBuckets.targets.get(key) === newBuckets.targets.get(key);
  });
}

export function updateAllocation(rows, sectionId, groupId, slotOrTopic, topicOrPatch, maybePatch) {
  // Keep the public helper compatible with saved/older callers that passed
  // (rows, section, group, topic, patch) before slot IDs existed.
  const isBucket = slotOrTopic && typeof slotOrTopic === "object" && Object.hasOwn(slotOrTopic, "bucketId");
  const hasSlot = !isBucket && (typeof slotOrTopic === "string" || slotOrTopic === null);
  const slotId = hasSlot ? slotOrTopic : null;
  const bucketId = isBucket ? slotOrTopic.bucketId : null;
  const topic = (hasSlot || isBucket) ? topicOrPatch : slotOrTopic;
  const patch = (hasSlot || isBucket) ? maybePatch : topicOrPatch;
  const key = JSON.stringify([sectionId, groupId, slotId || null, bucketId, topic.textbook_id, topic.topic_id]);
  const matches = row => JSON.stringify([row.section_id, row.group_id, row.slot_id || null, row.bucket_id || null, row.textbook_id, row.topic_id]) === key;
  if (patch === null) return rows.filter(row => !matches(row));
  const existing = rows.find(matches);
  const next = { section_id: sectionId, group_id: groupId, slot_id: slotId || null, textbook_id: topic.textbook_id,
    topic_id: topic.topic_id, bucket_id: bucketId, count: 1, difficulty: null, diagrams: false, ...existing, ...patch };
  return existing ? rows.map(row => matches(row) ? next : row) : [...rows, next];
}
