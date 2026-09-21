import React from "react";
import MathText from "@/components/MathText";

const itemPattern = /(?:^|\n)\s*(\([A-Za-z0-9]+\))\s*/g;

function listItems(text) {
  const matches = [...text.matchAll(itemPattern)];
  if (!matches.length) return [];
  return matches.map((match, index) => ({
    label: match[1],
    text: text.slice(match.index + match[0].length, matches[index + 1]?.index ?? text.length).trim(),
  })).filter(item => item.text);
}

function parseMatchingQuestion(text) {
  const columnOne = /\bColumn\s*(?:I|1)\s*:\s*/i.exec(text);
  if (!columnOne) return null;
  const columnTwoPattern = /\bColumn\s*(?:II|2)\s*:\s*/i;
  const afterOne = text.slice(columnOne.index + columnOne[0].length);
  const columnTwo = columnTwoPattern.exec(afterOne);
  if (!columnTwo) return null;

  const left = listItems(afterOne.slice(0, columnTwo.index));
  const afterTwo = afterOne.slice(columnTwo.index + columnTwo[0].length);
  const tailStart = /\n\s*(?:Select|Choose|Pick)\b/i.exec(afterTwo);
  const right = listItems(tailStart ? afterTwo.slice(0, tailStart.index) : afterTwo);
  if (!left.length || !right.length) return null;

  return {
    prompt: text.slice(0, columnOne.index).trim(),
    left,
    right,
    tail: tailStart ? afterTwo.slice(tailStart.index).trim() : "",
  };
}

export default function MatchTheFollowing({ text }) {
  const matching = parseMatchingQuestion(text || "");
  if (!matching) return <MathText text={text} />;

  const rows = Array.from({ length: Math.max(matching.left.length, matching.right.length) }, (_, index) => ({
    left: matching.left[index], right: matching.right[index],
  }));
  return <div className="match-following">
    {matching.prompt && <div className="match-following-prompt"><MathText text={matching.prompt} /></div>}
    <table className="match-following-table">
      <thead><tr><th scope="col" className="match-following-number" aria-label="Row number" /><th scope="col">Column-I</th><th scope="col">Column-II</th></tr></thead>
      <tbody>{rows.map((row, index) => <tr key={index}><th scope="row" className="match-following-number">{index + 1}</th><td>{row.left && <><strong>{row.left.label}</strong> <MathText text={row.left.text} /></>}</td><td>{row.right && <><strong>{row.right.label}</strong> <MathText text={row.right.text} /></>}</td></tr>)}</tbody>
    </table>
    {matching.tail && <div className="match-following-tail"><MathText text={matching.tail} /></div>}
  </div>;
}
