import React from "react";
import { InlineMath, BlockMath } from "react-katex";
import "katex/dist/katex.min.css";

/**
 * Render a string that may contain LaTeX delimited by `$...$` (inline) or
 * `$$...$$` (block). Plain text segments are preserved as-is, math segments
 * are rendered via KaTeX.
 *
 * This is the last line of defence, not the first. backend/latex_repair.py
 * checks and repairs the markup before a paper is ever marked ready; what
 * survives to here is whatever that pass could not fix safely. Since the
 * downloaded PDF is the browser's print of this DOM (see lib/print.js), a
 * KaTeX error rendered here ends up in the teacher's PDF — so nothing in this
 * file is allowed to render an error.
 */

/** `\(...\)` and `\[...\]` are never asked for by the prompts but models emit
 *  them out of habit. Normalising here means they render instead of showing up
 *  as literal backslashes. Mirrors latex_repair._alt_delims. */
const normalizeDelimiters = (text) =>
  text
    .replace(/\\\[/g, "$$$$")
    .replace(/\\\]/g, "$$$$")
    .replace(/\\\(/g, "$")
    .replace(/\\\)/g, "$");

/** A math span holding nothing but digits, commas, dots and spaces carries no
 *  maths — "$60$" is a stray delimiter, not an equation. Same heuristic as
 *  pdf_utils._math_to_paragraph_html; the delimiters are dropped. */
const isPureNumber = (inner) => /^[\d,.\s]+$/.test(inner);

/** The gap between two currency signs: "costs $10 and a hat costs $20" scans as
 *  a math span holding "10 and a hat costs ", which KaTeX will happily typeset
 *  as italic prose. A number ending at a word boundary followed by ordinary
 *  words with no math characters is a price, not an expression.
 *  Mirrors latex_repair._is_currency_gap — keep the two in step. */
const isCurrencyGap = (inner) =>
  inner.length < 60 &&
  /^\d[\d,]*(?:\.\d+)?\b[^\\^_{}$+=*/<>-]*$/.test(inner) &&
  /[A-Za-z]/.test(inner);

const splitMath = (raw) => {
  if (!raw) return [];
  const text = normalizeDelimiters(raw);
  const parts = [];
  let i = 0;
  while (i < text.length) {
    // A backslash escapes the next character, so `\$` is a literal dollar
    // rather than a delimiter. Kept in sync with latex_repair._scan.
    if (text[i] === "\\" && i + 1 < text.length) {
      const prev = parts[parts.length - 1];
      const chunk = text[i + 1] === "$" ? "$" : text.slice(i, i + 2);
      if (prev && prev.kind === "text") prev.value += chunk;
      else parts.push({ kind: "text", value: chunk });
      i += 2;
    } else if (text.startsWith("$$", i)) {
      const end = text.indexOf("$$", i + 2);
      if (end === -1) {
        parts.push({ kind: "text", value: text.slice(i) });
        break;
      }
      parts.push({ kind: "block", value: text.slice(i + 2, end) });
      i = end + 2;
    } else if (text[i] === "$") {
      const end = text.indexOf("$", i + 1);
      if (end === -1) {
        parts.push({ kind: "text", value: text.slice(i) });
        break;
      }
      const inner = text.slice(i + 1, end);
      if (isPureNumber(inner)) {
        parts.push({ kind: "text", value: inner });
      } else if (isCurrencyGap(inner)) {
        // Put the dollars back — they were currency, not delimiters.
        parts.push({ kind: "text", value: "$" + inner + "$" });
      } else {
        parts.push({ kind: "inline", value: inner });
      }
      i = end + 1;
    } else {
      const next = text.indexOf("$", i);
      const stop = next === -1 ? text.length : next;
      const escape = text.indexOf("\\", i);
      const cut = escape !== -1 && escape < stop ? escape : stop;
      parts.push({ kind: "text", value: text.slice(i, cut) });
      i = cut;
      if (cut === text.length) break;
    }
  }
  return parts;
};

/**
 * Passing `renderError` is what actually suppresses KaTeX's red error markup.
 * react-katex sets `throwOnError: !!renderError` and calls this from its own
 * catch block, so without it KaTeX renders the error message itself — the
 * try/catch this file used to carry never ran, because the throw happens inside
 * react-katex rather than in our render scope.
 */
const fallback = (value, block) =>
  block ? (
    <pre className="math-fallback">{value}</pre>
  ) : (
    <code className="math-fallback">{value}</code>
  );

export default function MathText({ text, className = "" }) {
  const segments = splitMath(text || "");
  return (
    <span className={["math-text", className].filter(Boolean).join(" ")}>
      {segments.map((seg, i) => {
        if (seg.kind === "inline") {
          return (
            <InlineMath
              key={i}
              math={seg.value}
              renderError={() => fallback(seg.value, false)}
            />
          );
        }
        if (seg.kind === "block") {
          return (
            <BlockMath
              key={i}
              math={seg.value}
              renderError={() => fallback(seg.value, true)}
            />
          );
        }
        return (
          <span key={i} style={{ whiteSpace: "pre-wrap" }}>
            {seg.value}
          </span>
        );
      })}
    </span>
  );
}
