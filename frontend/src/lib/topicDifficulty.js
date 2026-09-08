export const topicKey = (bookId, name, parent = "") =>
  JSON.stringify([bookId, parent, name]);

export function buildTopicPayload(selected, levels, paperDifficulty) {
  const bookIds = [...new Set(selected.map((topic) => topic.textbook_id))];
  const names = selected.map((topic) => topic.parent_topic
    ? `${topic.parent_topic} > ${topic.name}` : topic.name);
  return selected.map(({ key, ...topic }, index) => ({
    ...topic,
    // Generation and coverage tracking use names as identifiers.
    name: names.filter((name) => name === names[index]).length > 1
      ? `${names[index]} (textbook ${bookIds.indexOf(topic.textbook_id) + 1})`
      : names[index],
    difficulty: levels[key] ||
      (topic.parent_topic && levels[topicKey(topic.textbook_id, topic.parent_topic)]) ||
      paperDifficulty,
  }));
}
