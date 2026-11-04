import React, { useState } from "react";
import { useNavigate } from "react-router-dom";
import { ArrowRight } from "@phosphor-icons/react";
import { useAuth } from "@/context/AuthContext";
import ProfileFields from "@/components/ProfileFields";

export default function CompleteProfile() {
  const { user, updateBodhaProfile } = useAuth();
  const navigate = useNavigate();
  const [form, setForm] = useState({ fullName: user?.full_name || "", schoolName: user?.school_name || "", board: user?.board || "", boardOtherName: user?.board_other_name || "" });
  const [error, setError] = useState("");
  const [saving, setSaving] = useState(false);
  const update = (event) => setForm((previous) => ({ ...previous, [event.target.name]: event.target.value }));
  const submit = async (event) => {
    event.preventDefault(); setError(""); setSaving(true);
    try {
      await updateBodhaProfile({ full_name: form.fullName, school_name: form.schoolName, board: form.board, board_other_name: form.boardOtherName });
      navigate("/", { replace: true });
    } catch (err) {
      setError(err?.response?.data?.detail || "Could not save your profile. Please try again.");
    } finally { setSaving(false); }
  };
  return <main className="min-h-screen flex items-center justify-center bg-[#FAFAFA] p-6"><section className="w-full max-w-md qp-card hard-shadow-static">
    <h1 className="font-display text-4xl mb-2">Tell us about your school.</h1><p className="text-sm text-neutral-600 mb-6">One last step before your Bodha AI workspace is ready.</p>
    {error && <p className="mb-5 border-2 border-red-700 bg-red-50 p-3 text-sm text-red-800" role="alert">{error}</p>}
    <form onSubmit={submit} className="space-y-4"><ProfileFields form={form} onChange={update} includeName /><button type="submit" disabled={saving} className="qp-btn qp-btn-primary w-full">{saving ? "Saving…" : "Continue"}<ArrowRight size={16} weight="bold" /></button></form>
  </section></main>;
}
