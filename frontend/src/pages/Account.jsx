import React, { useEffect, useState } from "react";
import { EnvelopeSimple, LockKey, UserCircle } from "@phosphor-icons/react";
import Header from "@/components/Header";
import ProfileFields from "@/components/ProfileFields";
import { api } from "@/lib/api";
import { useAuth } from "@/context/AuthContext";

const Stat = ({ label, value }) => <div className="border-2 border-black bg-white p-5"><div className="overline text-neutral-500 mb-2">{label}</div><div className="font-display text-4xl">{value ?? "—"}</div></div>;

export default function Account() {
  const { user, updateBodhaProfile, requestPasswordReset, hasPasswordProvider } = useAuth();
  const [account, setAccount] = useState(null);
  const [form, setForm] = useState(null);
  const [notice, setNotice] = useState("");
  const [error, setError] = useState("");
  const [saving, setSaving] = useState(false);
  useEffect(() => { api.get("/auth/account").then(({ data }) => { setAccount(data); setForm({ fullName: data.user.full_name, schoolName: data.user.school_name, board: data.user.board, boardOtherName: data.user.board_other_name }); }).catch(() => setError("Could not load account details.")); }, []);
  const update = (event) => setForm((previous) => ({ ...previous, [event.target.name]: event.target.value }));
  const save = async (event) => { event.preventDefault(); setNotice(""); setError(""); setSaving(true); try { const updated = await updateBodhaProfile({ full_name: form.fullName, school_name: form.schoolName, board: form.board, board_other_name: form.boardOtherName }); setAccount((current) => ({ ...current, user: updated })); setNotice("Profile saved."); } catch (err) { setError(err?.response?.data?.detail || "Could not save your profile."); } finally { setSaving(false); } };
  const resetPassword = async () => { setError(""); try { await requestPasswordReset(user.email); setNotice("If your account supports passwords, a reset link is on its way."); } catch (err) { setError(err.message || "Could not send a reset email."); } };
  if (!account || !form) return <div className="min-h-screen bg-[#FAFAFA]"><Header /><main className="max-w-5xl mx-auto p-6 md:p-12">{error || "Loading account…"}</main></div>;
  const { stats } = account;
  return <div className="min-h-screen bg-[#FAFAFA]"><Header /><main className="max-w-5xl mx-auto p-6 md:p-12" data-testid="account-page">
    <div className="mb-10"><h1 className="font-display text-5xl">Your profile.</h1><p className="text-neutral-600 mt-2">Manage your account and see your Bodha AI activity.</p></div>
    <section className="grid md:grid-cols-3 gap-4 mb-10">{stats.kind === "student" ? <><Stat label="Total tests" value={stats.total_tests} /><Stat label="Submitted" value={stats.submitted_tests} /><Stat label="Avg score" value={stats.average_score == null ? "—" : `${stats.average_score}%`} /></> : <><Stat label="Textbooks indexed" value={stats.textbooks_indexed} /><Stat label="Papers generated" value={stats.papers_generated} /><Stat label="Questions saved" value={stats.questions_saved} /></>}</section>
    <section className="grid lg:grid-cols-2 gap-6"><form onSubmit={save} className="qp-card hard-shadow-static space-y-4"><div className="flex items-center gap-2"><UserCircle size={22} weight="bold" /><h2 className="font-display text-3xl">Profile details</h2></div>{notice && <p className="border-2 border-green-700 bg-green-50 p-3 text-sm text-green-900">{notice}</p>}{error && <p className="border-2 border-red-700 bg-red-50 p-3 text-sm text-red-800">{error}</p>}<div><label className="qp-label">Email address</label><div className="qp-input bg-neutral-100 text-neutral-600">{account.user.email}</div><p className="mt-1 text-xs text-neutral-500">Email is managed securely through Firebase.</p></div><ProfileFields form={form} onChange={update} includeName /><button type="submit" disabled={saving} className="qp-btn qp-btn-primary w-full">{saving ? "Saving…" : "Save profile"}</button></form>
      <section className="qp-card hard-shadow-static h-fit"><div className="flex items-center gap-2 mb-3"><LockKey size={22} weight="bold" /><h2 className="font-display text-3xl">Security</h2></div><p className="text-sm text-neutral-600 mb-5">{hasPasswordProvider() ? "Send a secure password-reset link to your email." : "You currently sign in with Google. Google manages that password."}</p>{hasPasswordProvider() ? <button type="button" onClick={resetPassword} className="qp-btn qp-btn-secondary w-full"><EnvelopeSimple size={16} weight="bold" /> Send reset email</button> : <div className="border border-neutral-300 p-3 text-sm text-neutral-600">Use your Google Account settings to change your Google password.</div>}<dl className="mt-6 text-sm"><dt className="overline text-neutral-500">Role</dt><dd className="font-medium mb-3">{account.user.role}</dd><dt className="overline text-neutral-500">Member since</dt><dd>{account.user.created_at ? new Date(account.user.created_at).toLocaleDateString() : "—"}</dd></dl></section>
    </section>
  </main></div>;
}
