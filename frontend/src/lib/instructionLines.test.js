import { instructionLines } from "./instructionLines";

test("keeps multi-digit numbering and session years intact", () => {
  expect(instructionLines("9. Choose one.\n10. Show working.\n11. Use the 2026-27 paper.\n12. Finish."))
    .toEqual(["9. Choose one.", "10. Show working.", "11. Use the 2026-27 paper.", "12. Finish."]);
});
test("separates inline numbered, roman and bullet instructions", () => {
  expect(instructionLines("1. Read. 2. Answer. 10. Check."))
    .toEqual(["1. Read.", "2. Answer.", "10. Check."]);
  expect(instructionLines("(i) Read. (ii) Answer. • Check."))
    .toEqual(["(i) Read.", "(ii) Answer.", "• Check."]);
});
