import React, { useEffect, useState } from "react";
import { api } from "@/lib/api";
import Header from "@/components/Header";
import {
  Trophy,
  ArrowRight,
  Books,
  Lightning,
} from "@phosphor-icons/react";

// Curated quick-start templates — one click to create a competitive exam
// pre-tuned for the right model chain + system prompt overlay (backend side).
const PRESETS = [
  {
    code: "JEE_MAINS",
    name: "JEE Mains 2026",
    description: "Physics, Chemistry, Maths — NTA pattern, MCQ + numerical.",
    accent: "#002FA7",
  },
  {
    code: "JEE_ADV",
    name: "JEE Advanced 2026",
    description: "Multi-concept reasoning, MCQ + numerical, IIT-level depth.",
    accent: "#7C3AED",
  },
  {
    code: "CAT",
    name: "CAT 2026 — IIM",
    description: "LRDI, VARC, QA — long-context passage + reasoning.",
    accent: "#0891B2",
  },
  {
    code: "UPSC",
    name: "UPSC CSE — GS",
    description: "Analytical GS questions in 'discuss / critically examine' format.",
    accent: "#B45309",
  },
  {
    code: "NEET",
    name: "NEET UG — PCB",
    description: "Strictly NCERT-anchored Biology, Physics, Chemistry MCQs.",
    accent: "#15803D",
  },
];

export default function CompetitiveExams() {
  const [exams, setExams] = useState([]);
  const [loading, setLoading] = useState(true);

  const load = async () => {
    try {
      const { data } = await api.get("/competitive-exams");
      setExams(data || []);
    } finally {
      setLoading(false);
    }
  };

  useEffect(() => {
    load();
  }, []);

  return (
    <div className="min-h-screen bg-[#FAFAFA]">
      <Header />
      <main className="max-w-6xl mx-auto p-6 md:p-12" data-testid="competitive-exams-page">
        <div className="flex items-end justify-between flex-wrap gap-4 mb-10">
          <div>
            <div className="overline text-neutral-500 mb-2">// COMPETITIVE EXAMS</div>
            <h1 className="font-display text-5xl">
              Train against <span className="text-[#002FA7]">past papers.</span>
            </h1>
            <p className="mt-2 max-w-xl font-bold text-red-700">
              in progress, stay tuned
            </p>
          </div>
        </div>

        <div
          className="border-2 border-black bg-white p-5 mb-8 hard-shadow-static"
          data-testid="quick-create-grid"
        >
          <div className="flex items-center gap-2 mb-3">
            <Lightning size={20} weight="fill" className="text-[#FFC300]" />
            <h2 className="font-display text-2xl">Quick-start exam libraries</h2>
          </div>
          <p className="text-sm text-neutral-600 mb-4">
            One click creates a pre-tuned library: each preset routes to the
            model best suited for that exam (e.g. JEE Adv → o3-pro, UPSC →
            Claude Opus 4.6, CAT → Gemini 3.1 Pro) and uses an exam-specific
            system prompt overlay.
          </p>
          <div className="grid sm:grid-cols-2 lg:grid-cols-3 gap-3">
            {PRESETS.map((p) => (
              <button
                key={p.code}
                type="button"
                disabled
                aria-disabled="true"
                data-testid={`preset-${p.code}`}
                className="text-left border-2 border-black p-4 bg-white opacity-45 cursor-not-allowed"
              >
                <div className="flex items-center gap-2 mb-1">
                  <span
                    className="w-2 h-2 rounded-full"
                    style={{ background: p.accent }}
                  />
                  <span className="font-display text-lg leading-tight">{p.name}</span>
                </div>
                <div className="text-xs text-neutral-600">{p.description}</div>
                <div className="overline mt-2 text-[10px] text-neutral-500">
                  {p.code.replace("_", " ")}
                </div>
              </button>
            ))}
          </div>
        </div>

        <div className="grid lg:grid-cols-[1fr_360px] gap-6">
          <div>
            <div className="overline text-neutral-500 mb-3">// YOUR LIBRARY</div>
            {loading ? (
              <div className="text-neutral-500 text-sm">Loading…</div>
            ) : exams.length === 0 ? (
              <div className="border-2 border-dashed border-neutral-300 p-12 text-center bg-white">
                <Books size={36} className="mx-auto text-neutral-400 mb-3" />
                <p className="text-neutral-600 text-sm">Create your first test</p>
              </div>
            ) : (
              <div className="space-y-3">
                {exams.map((e) => (
                  <article
                    key={e.id}
                    data-testid={`exam-card-${e.id}`}
                    aria-disabled="true"
                    className="border-2 border-black bg-white p-5 hard-shadow-static opacity-45 cursor-not-allowed"
                  >
                    <div className="flex items-start justify-between gap-4 flex-wrap">
                      <div>
                        <div className="flex items-center gap-2 mb-1 flex-wrap">
                          <Trophy size={18} weight="fill" className="text-[#FFC300]" />
                          <h3 className="font-display text-2xl leading-tight">{e.name}</h3>
                          {e.exam_type && e.exam_type !== "GENERIC" && (
                            <span
                              className="px-2 py-0.5 text-[10px] font-bold uppercase bg-black text-white"
                              data-testid={`exam-type-${e.exam_type}`}
                            >
                              {e.exam_type.replace("_", " ")}
                            </span>
                          )}
                        </div>
                        {e.description && (
                          <p className="text-sm text-neutral-600">{e.description}</p>
                        )}
                        <div className="text-xs text-neutral-500 mt-2">
                          {e.papers_count || 0} past paper(s) ·{" "}
                          {e.questions_count || 0} question(s) indexed
                        </div>
                      </div>
                      <ArrowRight size={20} weight="bold" className="text-neutral-400" />
                    </div>
                  </article>
                ))}
              </div>
            )}
          </div>

          <aside>
            <form
              onSubmit={(event) => event.preventDefault()}
              className="qp-card hard-shadow-static"
              data-testid="new-exam-form"
            >
              <fieldset disabled className="opacity-45 cursor-not-allowed">
                <div className="overline text-neutral-500 mb-2">// NEW EXAM</div>
                <h3 className="font-display text-2xl mb-3">Create exam</h3>
                <div className="mb-3">
                  <label className="qp-label">Name</label>
                  <input disabled className="qp-input" placeholder="JEE Main 2026" data-testid="new-exam-name" />
                </div>
                <div className="mb-3">
                  <label className="qp-label">Description</label>
                  <textarea disabled className="qp-input" rows={3} placeholder="Physics + Chemistry + Maths…" data-testid="new-exam-description" />
                </div>
                <div className="mb-3">
                  <label className="qp-label">Exam type</label>
                  <select disabled defaultValue="GENERIC" className="qp-input" data-testid="new-exam-type">
                    <option value="GENERIC">Generic (default model chain)</option>
                    <option value="JEE_MAINS">JEE Mains</option>
                    <option value="JEE_ADV">JEE Advanced</option>
                    <option value="CAT">CAT (IIM)</option>
                    <option value="UPSC">UPSC CSE</option>
                    <option value="NEET">NEET UG</option>
                  </select>
                </div>
                <p className="text-xs text-neutral-500 mb-4">
                  Exams and the papers you upload into them are private to your
                  account.
                </p>
                <button type="submit" disabled className="qp-btn qp-btn-primary w-full" data-testid="new-exam-submit">
                  Create exam
                </button>
              </fieldset>
            </form>
          </aside>
        </div>
      </main>
    </div>
  );
}
