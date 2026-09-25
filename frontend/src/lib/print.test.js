import { diagramsReady } from "./print";

test("blocks export while a branding logo is loading or failed", () => {
  document.body.innerHTML = '<div data-testid="branding-logo-loading"></div>';
  expect(diagramsReady()).toBe(false);
  document.body.innerHTML = '<div data-testid="branding-logo-failed"></div>';
  expect(diagramsReady()).toBe(false);
});

test("allows export when there are no pending print assets", () => {
  document.body.innerHTML = "<main></main>";
  expect(diagramsReady()).toBe(true);
});
