import { buildTopicPayload, topicKey } from "./topicDifficulty";

const item = (name, parent = "", book = "book-a") => ({
  key: topicKey(book, name, parent), name, parent_topic: parent,
  textbook_id: book, weight: 5,
});

test("subtopics inherit their parent even when only the subtopic is selected", () => {
  const selected = [item("Motion", "Mechanics")];
  const levels = { [topicKey("book-a", "Mechanics")]: "easy" };
  expect(buildTopicPayload(selected, levels, "hard")[0].difficulty).toBe("easy");
});

test("parent changes update inherited levels and preserve explicit subtopic choices", () => {
  const selected = [item("Mechanics"), item("Motion", "Mechanics"), item("Forces", "Mechanics")];
  const levels = { [selected[0].key]: "easy", [selected[2].key]: "hard" };
  expect(buildTopicPayload(selected, levels, "medium").map((t) => t.difficulty))
    .toEqual(["easy", "easy", "hard"]);
  levels[selected[0].key] = "medium";
  expect(buildTopicPayload(selected, levels, "easy").map((t) => t.difficulty))
    .toEqual(["medium", "medium", "hard"]);
  levels[selected[2].key] = "";
  expect(buildTopicPayload(selected, levels, "easy")[2].difficulty).toBe("medium");
});

test("paper difficulty is the fallback and repeated names remain independent", () => {
  const selected = [item("Basics", "Motion"), item("Basics", "Forces"), item("Basics", "Motion", "book-b")];
  const result = buildTopicPayload(selected, { [selected[0].key]: "hard" }, "easy");
  expect(result.map((t) => t.difficulty)).toEqual(["hard", "easy", "easy"]);
  expect(new Set(result.map((t) => t.name)).size).toBe(3);
  expect(result.every((t) => t.key === undefined && t.weight === 5)).toBe(true);
});
