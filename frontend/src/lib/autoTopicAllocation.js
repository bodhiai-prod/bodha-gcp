import { allocationBuckets } from "@/lib/sectionBlueprint";

const randomIndex = (length, random) => Math.min(length - 1, Math.max(0, Math.floor(random() * length)));

export function catalogTopics(bookIds, details) {
  return bookIds.flatMap((bookId) => (details[bookId]?.topic_catalog || []))
    .filter((topic) => topic?.textbook_id && topic?.topic_id && topic?.name)
    .filter((topic, index, rows) => rows.findIndex((candidate) =>
      candidate.textbook_id === topic.textbook_id && candidate.topic_id === topic.topic_id
    ) === index);
}

export function shuffled(items, random = Math.random) {
  const next = [...items];
  for (let index = next.length - 1; index > 0; index -= 1) {
    const other = randomIndex(index + 1, random);
    [next[index], next[other]] = [next[other], next[index]];
  }
  return next;
}

// Fill each target exactly while using a shuffled catalog before starting a
// second pass. Counts are intentionally random, but never consume enough
// capacity to prevent the remaining distinct catalog rows from appearing.
function allocateTargets(targets, candidates, makeRow, random) {
  if (!candidates.length) return [];
  const totalCapacity = targets.reduce((total, target) => total + target.count, 0);
  const distinctNeeded = Math.min(candidates.length, totalCapacity);
  let selectedDistinct = 0;
  let remainingCapacity = totalCapacity;
  let pool = shuffled(candidates, random);
  let cursor = 0;
  const rows = [];

  const nextTopic = () => {
    if (cursor === pool.length) {
      pool = shuffled(candidates, random);
      cursor = 0;
    }
    return pool[cursor++];
  };

  targets.forEach((target) => {
    let remaining = target.count;
    const rowsByTopic = new Map();
    while (remaining > 0) {
      const topic = nextTopic();
      const distinctRemaining = Math.max(0, distinctNeeded - selectedDistinct - 1);
      const maximum = Math.min(remaining, remainingCapacity - distinctRemaining);
      const count = 1 + randomIndex(Math.max(1, maximum), random);
      const key = `${topic.textbook_id}:${topic.topic_id}`;
      const existing = rowsByTopic.get(key);
      if (existing) existing.count += count;
      else {
        const row = makeRow(target, topic, count);
        rowsByTopic.set(key, row);
        rows.push(row);
      }
      if (selectedDistinct < distinctNeeded) selectedDistinct += 1;
      remaining -= count;
      remainingCapacity -= count;
    }
  });
  return rows;
}

export function generateFormatTopicAllocations(formats, bookIds, details, random = Math.random) {
  const targets = formats.filter((format) => Number(format.count) > 0)
    .map((format) => ({ format: format.key, count: Number(format.count) }));
  return allocateTargets(targets, catalogTopics(bookIds, details), (target, topic, count) => ({
    format: target.format,
    textbook_id: topic.textbook_id,
    topic_id: topic.topic_id,
    name: topic.name,
    parent_topic: topic.parent_topic || "",
    count,
    difficulty: null,
    diagrams: false,
  }), random);
}

export function generateBlueprintTopicAllocations(blueprint, bookIds, details, random = Math.random) {
  const targets = (blueprint?.sections || []).flatMap((section) => section.groups.flatMap((group) =>
    allocationBuckets(group).map((bucket) => ({
      sectionId: section.id,
      groupId: group.id,
      bucketId: bucket.id,
      count: bucket.slots.length,
    }))
  ));
  return allocateTargets(targets, catalogTopics(bookIds, details), (target, topic, count) => ({
    section_id: target.sectionId,
    group_id: target.groupId,
    slot_id: null,
    bucket_id: target.bucketId,
    textbook_id: topic.textbook_id,
    topic_id: topic.topic_id,
    count,
    difficulty: null,
    diagrams: false,
  }), random);
}
