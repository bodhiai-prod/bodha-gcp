import React, { useEffect, useState } from "react";
import { Link, useNavigate, useSearchParams } from "react-router-dom";
import { ArrowRight, Eye, EyeSlash } from "@phosphor-icons/react";
import { confirmPasswordReset, verifyPasswordResetCode } from "firebase/auth";
import { requireFirebaseAuth } from "@/lib/firebase";

export default function ResetPassword() {
  const [params] = useSearchParams();
  const navigate = useNavigate();
  const code = params.get("oobCode") || "";
  const [status, setStatus] = useState("checking");
  const [password, setPassword] = useState("");
  const [confirm, setConfirm] = useState("");
  const [visible, setVisible] = useState(false);
  const [error, setError] = useState("");

  useEffect(() => {
    if (!code) return setStatus("invalid");
    verifyPasswordResetCode(requireFirebaseAuth(), code).then(() => setStatus("ready")).catch(() => setStatus("invalid"));
  }, [code]);

  const submit = async (event) => {
    event.preventDefault();
    setError("");
    if (password.length < 8) return setError("Use at least 8 characters.");
    if (password !== confirm) return setError("Passwords do not match.");
    setStatus("submitting");
    try {
      await confirmPasswordReset(requireFirebaseAuth(), code, password);
      setStatus("complete");
      window.setTimeout(() => navigate("/login", { replace: true }), 1500);
    } catch {
      setStatus("invalid");
    }
  };

  return <main className="min-h-screen flex items-center justify-center bg-[#FAFAFA] p-6"><section className="w-full max-w-md qp-card hard-shadow-static">
    <div className="overline text-neutral-500 mb-2">// AUTH / RESET</div><h1 className="font-display text-4xl mb-2">Choose a password.</h1>
    {status === "checking" && <p className="text-neutral-600">Verifying your reset link…</p>}
    {status === "invalid" && <><p className="text-neutral-700">This reset link is invalid, expired, or has already been used.</p><p className="mt-6 text-sm"><Link to="/forgot-password" className="font-bold text-[#002FA7] underline">Request another reset link</Link></p></>}
    {status === "complete" && <p className="text-neutral-700">Your password has been reset. Redirecting you to sign in…</p>}
    {(status === "ready" || status === "submitting") && <form onSubmit={submit} className="space-y-4 mt-6">
      {error && <p className="border-2 border-red-700 bg-red-50 p-3 text-sm text-red-800" role="alert">{error}</p>}
      <div><label className="qp-label" htmlFor="new-password">New password</label><div className="relative"><input id="new-password" type={visible ? "text" : "password"} autoComplete="new-password" required minLength="8" value={password} onChange={(event) => setPassword(event.target.value)} className="qp-input pr-11" /><button type="button" className="absolute inset-y-0 right-0 px-3" onClick={() => setVisible(!visible)} aria-label={visible ? "Hide password" : "Show password"}>{visible ? <EyeSlash size={20} /> : <Eye size={20} />}</button></div></div>
      <div><label className="qp-label" htmlFor="confirm-password">Confirm password</label><input id="confirm-password" type={visible ? "text" : "password"} autoComplete="new-password" required minLength="8" value={confirm} onChange={(event) => setConfirm(event.target.value)} className="qp-input" /></div>
      <button type="submit" disabled={status === "submitting"} className="qp-btn qp-btn-primary w-full">{status === "submitting" ? "Resetting…" : "Reset password"}<ArrowRight size={16} weight="bold" /></button>
    </form>}
  </section></main>;
}
