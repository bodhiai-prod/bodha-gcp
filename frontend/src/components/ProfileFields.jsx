import React from "react";

export const BOARD_OPTIONS = ["CBSE", "ICSE", "West Bengal Board", "Other"];

export default function ProfileFields({ form, onChange, includeName = false }) {
  return <>
    {includeName && <div><label className="qp-label" htmlFor="fullName">Full name</label><input id="fullName" name="fullName" autoComplete="name" required value={form.fullName} onChange={onChange} className="qp-input" /></div>}
    <div><label className="qp-label" htmlFor="schoolName">School name</label><input id="schoolName" name="schoolName" autoComplete="organization" required value={form.schoolName} onChange={onChange} className="qp-input" /></div>
    <div><label className="qp-label" htmlFor="board">Primary board</label><select id="board" name="board" required value={form.board} onChange={onChange} className="qp-input"><option value="" disabled>Select your board</option>{BOARD_OPTIONS.map((board) => <option key={board} value={board}>{board}</option>)}</select></div>
    {form.board === "Other" && <div><label className="qp-label" htmlFor="boardOtherName">Board name</label><input id="boardOtherName" name="boardOtherName" required value={form.boardOtherName} onChange={onChange} className="qp-input" /></div>}
  </>;
}
