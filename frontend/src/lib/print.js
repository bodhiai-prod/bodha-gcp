/**
 * Print helpers.
 *
 * The paper and solution PDFs are produced by the browser's own print engine
 * against the live DOM (see the `@media print` block in index.css) rather than
 * by the backend ReportLab renderer, so the saved PDF matches what is on
 * screen — same fonts, same KaTeX math, same badges.
 */

/**
 * Sanitise a title for use as a filename. Mirrors the backend's rule in
 * server.py so both download paths produce consistent names.
 */
export const safeFilename = (title, fallback = "paper") =>
  (title || "").replace(/[^a-zA-Z0-9_\- ]/g, "").trim().slice(0, 60) || fallback;

/**
 * Open the browser's print dialog with `title` as the document title.
 *
 * Chrome derives the "Save as PDF" filename from document.title, which would
 * otherwise be the app's static title for every paper. The original title is
 * restored once printing finishes.
 */
export function printDocument(title) {
  const previous = document.title;
  const name = safeFilename(title);
  if (name) document.title = name;

  let restored = false;
  const restore = () => {
    if (restored) return;
    restored = true;
    document.title = previous;
    window.removeEventListener("afterprint", restore);
  };

  window.addEventListener("afterprint", restore);
  try {
    window.print();
  } finally {
    // Safari does not always fire `afterprint`; restore on a timer as well.
    setTimeout(restore, 1000);
  }
}

/**
 * True when every diagram on the page has finished loading.
 *
 * Diagrams are fetched as blob URLs so the auth token can be sent via header,
 * which means a diagram whose fetch is still in flight prints as a blank space
 * with no error. Callers should block printing until this returns true.
 *
 * Diagrams the backend is still generating (`diagram-pending`) are deliberately
 * not covered here — that state can last a while, the page polls for it, and
 * the placeholder is hidden in print anyway.
 */
export function diagramsReady(root = document) {
  if (root.querySelector('[data-testid="diagram-loading"]')) return false;
  const imgs = root.querySelectorAll('img[data-testid^="diagram-"]');
  return Array.from(imgs).every((img) => img.complete && img.naturalWidth > 0);
}
