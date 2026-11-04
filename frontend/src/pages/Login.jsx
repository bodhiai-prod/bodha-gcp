import React, { useEffect, useState } from "react";
import { Link, useNavigate } from "react-router-dom";
import { ArrowRight, Eye, EyeSlash } from "@phosphor-icons/react";
import { useAuth } from "@/context/AuthContext";
import ProfileFields from "@/components/ProfileFields";

function authError(error) {
  return error?.message || "Something went wrong. Please try again.";
}

function PasswordField({ id, label, value, onChange, autoComplete, hint }) {
  const [visible, setVisible] = useState(false);
  return <div>
    <label className="qp-label" htmlFor={id}>{label}</label>
    <div className="relative">
      <input id={id} name={id} type={visible ? "text" : "password"} autoComplete={autoComplete} minLength="8" required value={value} onChange={onChange} className="qp-input pr-11" />
      <button type="button" onClick={() => setVisible(!visible)} className="absolute inset-y-0 right-0 px-3 text-neutral-700 hover:text-[#002FA7]" aria-label={visible ? `Hide ${label.toLowerCase()}` : `Show ${label.toLowerCase()}`} aria-pressed={visible}>
        {visible ? <EyeSlash size={20} /> : <Eye size={20} />}
      </button>
    </div>
    {hint && <p className="mt-1 text-xs text-neutral-500">{hint}</p>}
  </div>;
}

export default function Login({ mode = "login" }) {
  const isRegister = mode === "register";
  const { user, loading, loginWithGoogle, loginWithPassword, registerWithPassword } = useAuth();
  const navigate = useNavigate();
  const [form, setForm] = useState({ fullName: "", email: "", password: "", confirmPassword: "", schoolName: "", board: "", boardOtherName: "" });
  const [error, setError] = useState("");
  const [submitting, setSubmitting] = useState(false);
  const update = (event) => setForm((previous) => ({ ...previous, [event.target.name]: event.target.value }));

  useEffect(() => {
    if (user && !loading) navigate(user.profile_complete ? "/" : "/complete-profile", { replace: true });
  }, [user, loading, navigate]);

  const submit = async (event) => {
    event.preventDefault();
    setError("");
    if (isRegister && form.password !== form.confirmPassword) return setError("Passwords do not match.");
    setSubmitting(true);
    try {
      if (isRegister) await registerWithPassword(form.fullName, form.email, form.password, { school_name: form.schoolName, board: form.board, board_other_name: form.boardOtherName });
      else await loginWithPassword(form.email, form.password);
      navigate("/", { replace: true });
    } catch (requestError) {
      setError(authError(requestError));
    } finally {
      setSubmitting(false);
    }
  };
  const continueWithGoogle = () => {
    if (isRegister && (!form.fullName.trim() || !form.schoolName.trim() || !form.board || (form.board === "Other" && !form.boardOtherName.trim()))) {
      setError("Complete your name, school, and board before continuing with Google.");
      return;
    }
    loginWithGoogle(isRegister ? { full_name: form.fullName, school_name: form.schoolName, board: form.board, board_other_name: form.boardOtherName } : null).catch((err) => setError(authError(err)));
  };

  return <div className="min-h-screen grid lg:grid-cols-2 bg-[#FAFAFA]">
    <div className="hidden lg:block relative border-r-2 border-black" style={{ backgroundImage: "url(https://images.pexels.com/photos/22039140/pexels-photo-22039140.jpeg?auto=compress&cs=tinysrgb&dpr=2&h=650&w=940)", backgroundSize: "cover", backgroundPosition: "center" }}>
      <div className="absolute inset-0 bg-white/75" />
      <div className="absolute inset-0 p-12 flex flex-col justify-between">
        <div className="flex items-center gap-3"><img src="/assets/bodhaai-app-icon-512.png" alt="Bodha AI" className="w-12 h-12 rounded-xl" /><div><div className="brand-mark text-2xl leading-none">Bodha <span className="accent">AI</span></div><div className="overline text-neutral-700 mt-1">Question paper studio</div></div></div>
        <div className="max-w-md"><h1 className="font-display text-5xl lg:text-6xl leading-[0.9] text-black">Design exams<br /><span className="text-[#002FA7]">algorithmically.</span></h1><p className="mt-6 text-neutral-700 max-w-sm">Upload any textbook. Extract topics automatically. Generate original, calibrated question papers — with multi-provider AI fallback built-in.</p></div>
        <div className="overline text-neutral-600">© Bodha AI — Academic control room</div>
      </div>
    </div>
    <div className="flex min-h-screen items-start justify-center p-4 lg:items-center lg:p-6"><section data-testid="login-form" className="w-full max-w-md qp-card hard-shadow-static lg:max-h-[calc(100vh-2rem)] lg:overflow-y-auto">
      <h2 className="font-display text-3xl mb-2">{isRegister ? "Create account." : "Sign in."}</h2>
      <p className="text-neutral-600 mb-4 text-sm">{isRegister ? "Use your email and password, or continue with Google." : "Sign in with your email and password, or continue with Google."}</p>
      {error && <p className="mb-3 border-2 border-red-700 bg-red-50 p-2 text-sm text-red-800" role="alert">{error}</p>}
      <form onSubmit={submit} className="space-y-3">
        {isRegister && <ProfileFields form={form} onChange={update} includeName />}
        <div><label className="qp-label" htmlFor="email">Email address</label><input id="email" name="email" type="email" autoComplete="email" required value={form.email} onChange={update} className="qp-input" /></div>
        <PasswordField id="password" label="Password" value={form.password} onChange={update} autoComplete={isRegister ? "new-password" : "current-password"} hint={isRegister ? "Use at least 8 characters." : null} />
        {isRegister && <PasswordField id="confirmPassword" label="Confirm password" value={form.confirmPassword} onChange={update} autoComplete="new-password" />}
        {!isRegister && <div className="text-right"><Link to="/forgot-password" className="text-sm font-medium text-[#002FA7] underline">Forgot password?</Link></div>}
        <button type="submit" disabled={submitting} className="qp-btn qp-btn-primary w-full" data-testid="email-auth-submit">{submitting ? "Please wait…" : isRegister ? "Create account" : "Sign in"}{!submitting && <ArrowRight size={16} weight="bold" />}</button>
      </form>
      <div className="flex items-center gap-3 my-4 text-neutral-500" aria-hidden="true"><div className="h-px bg-neutral-300 flex-1" /><span className="overline text-[10px]">or</span><div className="h-px bg-neutral-300 flex-1" /></div>
      <button type="button" onClick={continueWithGoogle} className="qp-btn qp-btn-secondary w-full" data-testid="google-login-button">Continue with Google <ArrowRight size={16} weight="bold" /></button>
      <p className="mt-4 text-sm text-neutral-600">{isRegister ? <>Already have an account? <Link to="/login" className="font-medium text-[#002FA7] underline">Sign in</Link></> : <>New to Bodha AI? <Link to="/register" className="font-medium text-[#002FA7] underline">Create an account</Link></>}</p>
    </section></div>
  </div>;
}
