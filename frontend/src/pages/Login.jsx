import React from "react";
import { useSearchParams } from "react-router-dom";
import { useAuth } from "@/context/AuthContext";
import { ArrowRight, BookOpen } from "@phosphor-icons/react";

export default function Login() {
  const { loginWithGoogle } = useAuth();
  const [params] = useSearchParams();
  const failed = params.get("error") === "google_sign_in_failed";

  return (
    <div className="min-h-screen grid lg:grid-cols-2 bg-[#FAFAFA]">
      {/* Left side: Image panel */}
      <div
        className="hidden lg:block relative border-r-2 border-black"
        style={{
          backgroundImage:
            "url(https://images.pexels.com/photos/22039140/pexels-photo-22039140.jpeg?auto=compress&cs=tinysrgb&dpr=2&h=650&w=940)",
          backgroundSize: "cover",
          backgroundPosition: "center",
        }}
      >
        <div className="absolute inset-0 bg-white/75" />
        <div className="absolute inset-0 p-12 flex flex-col justify-between">
          <div className="flex items-center gap-3">
            <div className="w-12 h-12 bg-black flex items-center justify-center">
              <BookOpen size={24} weight="fill" color="#FFC300" />
            </div>
            <div>
              <div className="brand-mark text-2xl leading-none">
                Bodha <span className="accent">AI</span>
              </div>
              <div className="overline text-neutral-700 mt-1">
                Question Paper Studio
              </div>
            </div>
          </div>

          <div className="max-w-md">
            <div className="overline text-neutral-700 mb-4">// VOLUME 01</div>
            <h1 className="font-display text-5xl lg:text-6xl leading-[0.9] text-black">
              Design exams
              <br />
              <span className="text-[#002FA7]">algorithmically.</span>
            </h1>
            <p className="mt-6 text-neutral-700 max-w-sm">
              Upload any textbook. Extract topics automatically. Generate
              original, calibrated question papers — with multi-provider AI
              fallback built-in.
            </p>
          </div>

          <div className="overline text-neutral-600">
            © Bodha AI — Academic control room
          </div>
        </div>
      </div>

      {/* Right side: Form */}
      <div className="flex items-center justify-center p-6 lg:p-12">
        <section data-testid="login-form" className="w-full max-w-md qp-card hard-shadow-static">
          <div className="overline text-neutral-500 mb-2">// AUTH / 01</div>
          <h2 className="font-display text-4xl mb-2">Sign in.</h2>
          <p className="text-neutral-600 mb-8 text-sm">
            Continue securely with your Google account.
          </p>
          {failed && (
            <p className="mb-6 border-2 border-red-700 bg-red-50 p-3 text-sm text-red-800" role="alert">
              Google sign-in could not be completed. Please try again.
            </p>
          )}

          <button
            type="button"
            onClick={loginWithGoogle}
            className="qp-btn qp-btn-primary w-full"
            data-testid="google-login-button"
          >
            Continue with Google
            <ArrowRight size={16} weight="bold" />
          </button>
          <p className="mt-6 text-sm text-neutral-600">
            Your Google account is verified before a Bodha AI session is created.
          </p>
        </section>
      </div>
    </div>
  );
}
