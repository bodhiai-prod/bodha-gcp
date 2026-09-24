import React, { useState } from "react";
import { Link } from "react-router-dom";
import { ArrowRight } from "@phosphor-icons/react";
import { useAuth } from "@/context/AuthContext";

export default function ForgotPassword() {
  const { requestPasswordReset } = useAuth();
  const [email, setEmail] = useState("");
  const [submitted, setSubmitted] = useState(false);
  const [error, setError] = useState("");
  const [sending, setSending] = useState(false);

  const submit = async (event) => {
    event.preventDefault();
    setError("");
    setSending(true);
    try {
      await requestPasswordReset(email);
      setSubmitted(true);
    } catch (requestError) {
      setError(requestError.message || "Unable to request a reset email.");
    } finally {
      setSending(false);
    }
  };

  return <main className="min-h-screen flex items-center justify-center bg-[#FAFAFA] p-6"><section className="w-full max-w-md qp-card hard-shadow-static">
    <div className="overline text-neutral-500 mb-2">// AUTH / RESET</div>
    <h1 className="font-display text-4xl mb-2">Reset password.</h1>
    {submitted ? <p className="text-neutral-700">If an account uses this email, a password-reset link is on its way. Check your inbox and spam folder.</p> : <>
      <p className="text-neutral-600 mb-6 text-sm">Enter your email and we’ll send a secure reset link.</p>
      {error && <p className="mb-5 border-2 border-red-700 bg-red-50 p-3 text-sm text-red-800" role="alert">{error}</p>}
      <form onSubmit={submit} className="space-y-4"><div><label className="qp-label" htmlFor="email">Email address</label><input id="email" type="email" autoComplete="email" required value={email} onChange={(event) => setEmail(event.target.value)} className="qp-input" /></div><button type="submit" disabled={sending} className="qp-btn qp-btn-primary w-full">{sending ? "Sending…" : "Send reset link"}<ArrowRight size={16} weight="bold" /></button></form>
    </>}
    <p className="mt-6 text-sm text-neutral-600"><Link to="/login" className="font-bold text-[#002FA7] underline">Return to sign in</Link></p>
  </section></main>;
}
