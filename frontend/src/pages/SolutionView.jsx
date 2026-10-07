import React, { useEffect, useState } from "react";
import { useParams, Link } from "react-router-dom";
import { api } from "@/lib/api";
import Header from "@/components/Header";
import { toast } from "sonner";
import { useConfirmation } from "@/context/ConfirmationContext";
import {
  ArrowLeft,
  Download,
  CaretDown,
  PencilSimple,
  FloppyDisk,
  X,
  CheckCircle,
  WarningCircle,
  Image as ImageIcon,
} from "@phosphor-icons/react";
import MathText from "@/components/MathText";
import MatchTheFollowing from "@/components/MatchTheFollowing";
import QuestionDiagram from "@/components/QuestionDiagram";
import BrandingLogo from "@/components/BrandingLogo";
import { printDocument, diagramsReady } from "@/lib/print";
import { formatMark } from "@/lib/exportFormat";
import { canonicalQuestionType, questionTypeLabel } from "@/lib/questionType";
import {
  DropdownMenu,
  DropdownMenuContent,
  DropdownMenuItem,
  DropdownMenuTrigger,
} from "@/components/ui/dropdown-menu";

const typeBadge = (t) => {
  if (t === "information") return "qp-badge qp-badge-blue";
  if (t === "application") return "qp-badge qp-badge-red";
  return "qp-badge qp-badge-yellow";
};

export default function SolutionView() {
  const confirm = useConfirmation();
  const { id } = useParams();
  const [paper, setPaper] = useState(null);
  const [editing, setEditing] = useState(false);
  const [draft, setDraft] = useState(null); // { sections: [ {title, answers: [{question_id, answer}]} ] }
  const [saving, setSaving] = useState(false);
  const [regenerating, setRegenerating] = useState(false);
  // null | "all" | a question_id — which retry button is mid-request.
  const [filling, setFilling] = useState(null);
  const [docxDownloading, setDocxDownloading] = useState(false);

  const load = async () => {
    const { data } = await api.get(`/papers/${id}`);
    setPaper(data);
  };

  useEffect(() => {
    load();
  }, [id]); // eslint-disable-line

  // Poll while solution is generating in the background.
  useEffect(() => {
    const sol = paper?.solution;
    if (!sol) return;
    if (sol.status === "generating") {
      const t = setInterval(load, 4000);
      return () => clearInterval(t);
    }
  }, [paper]); // eslint-disable-line

  const regenerate = async () => {
    if (
      paper?.solution &&
      paper.solution.status !== "generating" &&
      !(await confirm({
        title: "Regenerate solution?",
        description: "Your current edits will be overwritten.",
        confirmLabel: "Regenerate solution",
        tone: "danger",
      }))
    ) {
      return;
    }
    setRegenerating(true);
    try {
      await api.post(`/papers/${id}/solution/generate`);
      toast.success("Generation started — answers will fill in shortly");
      await load();
    } catch (err) {
      toast.error(err?.response?.data?.detail || "Failed");
    } finally {
      setRegenerating(false);
    }
  };

  // Fill in only the blank answers. Unlike regenerate() this cannot destroy
  // anything — the backend refuses to overwrite an answer that already has
  // content — so there is no confirm dialog.
  const generateMissing = async (qids) => {
    setFilling(qids ? qids[0] : "all");
    try {
      const { data } = await api.post(`/papers/${id}/solution/generate-missing`, {
        question_ids: qids || null,
      });
      if (data.queued === 0) {
        toast.info("Nothing missing — every question already has an answer");
      } else {
        toast.success(
          `Generating ${data.queued} missing answer${data.queued === 1 ? "" : "s"}...`
        );
      }
      await load();
    } catch (err) {
      toast.error(err?.response?.data?.detail || "Failed");
    } finally {
      setFilling(null);
    }
  };

  const enterEdit = () => {
    const sol = paper?.solution;
    if (!sol) return;
    // Mirror the paper rather than copying the stored solution verbatim, so a
    // question added after generation still gets a textarea instead of being
    // stuck on "(no answer)". The backend reconciles the same way.
    const prev = {};
    (sol.sections || []).forEach((s) =>
      (s.answers || []).forEach((a) => {
        prev[a.question_id] = a.answer || "";
      })
    );
    setDraft({
      ...sol,
      sections: (paper.sections || []).map((s) => ({
        title: s.title || "Section",
        answers: (s.questions || []).map((q) => ({
          question_id: q.id,
          answer: prev[q.id] || "",
        })),
      })),
    });
    setEditing(true);
  };

  const cancelEdit = () => {
    setDraft(null);
    setEditing(false);
  };

  const updateAnswer = (si, ai, value) => {
    const next = { ...draft };
    next.sections = next.sections.map((s, i) =>
      i === si
        ? {
            ...s,
            answers: s.answers.map((a, j) =>
              j === ai ? { ...a, answer: value } : a
            ),
          }
        : s
    );
    setDraft(next);
  };

  const saveEdit = async () => {
    setSaving(true);
    try {
      await api.patch(`/papers/${id}/solution`, {
        sections: draft.sections,
      });
      toast.success("Solution saved — AI will learn from your edits");
      setEditing(false);
      setDraft(null);
      await load();
    } catch (err) {
      toast.error(err?.response?.data?.detail || "Save failed");
    } finally {
      setSaving(false);
    }
  };

  // Saved via the browser's print engine rather than the backend renderer, so
  // the PDF keeps the fonts and KaTeX math shown on screen.
  const download = async () => {
    if (!diagramsReady()) {
      toast.error("Diagrams are still loading — try again in a moment");
      return;
    }
    const answeredIds = new Set((paper.solution?.sections || []).flatMap(section => (section.answers || []).filter(answer => answer.answer).map(answer => answer.question_id)));
    const unanswered = (paper.sections || []).flatMap(section => section.questions || []).filter(question => question.question && !answeredIds.has(question.id)).length;
    const generating = paper.solution?.status === "generating";
    if ((generating || unanswered > 0) && !(await confirm({
      title: "Download incomplete answer key?",
      description: `${generating ? "Answer-key generation is still in progress" : `${unanswered} answer${unanswered === 1 ? " is" : "s are"} still missing`}. Download the incomplete answer key anyway? Selecting Cancel keeps generation running.`,
      confirmLabel: "Download anyway",
    }))) return;
    printDocument(`${paper.title} - Solution`);
  };

  const downloadDocx = async () => {
    const answeredIds = new Set((paper.solution?.sections || []).flatMap(section => (section.answers || []).filter(answer => answer.answer).map(answer => answer.question_id)));
    const unanswered = (paper.sections || []).flatMap(section => section.questions || []).filter(question => question.question && !answeredIds.has(question.id)).length;
    const generating = paper.solution?.status === "generating";
    if ((generating || unanswered > 0) && !(await confirm({
      title: "Download incomplete answer key?",
      description: `${generating ? "Answer-key generation is still in progress" : `${unanswered} answer${unanswered === 1 ? " is" : "s are"} still missing`}. Download the incomplete answer key anyway?`,
      confirmLabel: "Download anyway",
    }))) return;
    setDocxDownloading(true);
    try {
      const { data } = await api.get(`/papers/${id}/solution/docx`, { responseType: "blob" });
      const url = URL.createObjectURL(data);
      const link = document.createElement("a");
      link.href = url;
      link.download = `${(paper.title || "paper").replace(/[^a-z0-9 _-]/gi, "").slice(0, 60) || "paper"} - Solution.docx`;
      link.click();
      URL.revokeObjectURL(url);
      toast.success("Solution DOCX downloaded");
    } catch (err) {
      toast.error(err?.response?.data?.detail || "Could not download solution DOCX");
    } finally {
      setDocxDownloading(false);
    }
  };

  if (!paper) {
    return (
      <div className="min-h-screen bg-[#FAFAFA]">
        <Header />
        <div className="max-w-5xl mx-auto p-12 text-neutral-500">
          Loading...
        </div>
      </div>
    );
  }

  const solution = editing ? draft : paper.solution;

  // Build answer lookup by question_id
  const answerByQid = {};
  (solution?.sections || []).forEach((s) =>
    (s.answers || []).forEach((a) => {
      answerByQid[a.question_id] = a.answer;
    })
  );

  // Derived from blank answers rather than solution.completed/total: those
  // counters are absent on solutions written before this existed, and on any
  // solution created by bulk-generate.
  const savedAnswers = {};
  (paper.solution?.sections || []).forEach((s) =>
    (s.answers || []).forEach((a) => {
      savedAnswers[a.question_id] = a.answer;
    })
  );
  const allQuestions = (paper.sections || []).flatMap((s) => s.questions || []);
  const missingQids = allQuestions
    .filter((q) => q.question && !savedAnswers[q.id])
    .map((q) => q.id);
  const isGenerating = paper.solution?.status === "generating";
  const busy = regenerating || filling !== null || isGenerating;

  let counter = 1;

  return (
    <div className="min-h-screen bg-[#FAFAFA]">
      <Header />
      <main className="max-w-5xl mx-auto p-6 md:p-12" data-testid="solution-view-page">
        {/* Toolbar */}
        <div className="no-print flex items-center justify-between mb-8 flex-wrap gap-3">
          <Link
            to={`/papers/${id}`}
            className="qp-btn-ghost inline-flex items-center gap-2"
            data-testid="back-to-paper"
          >
            <ArrowLeft size={16} weight="bold" /> Back to paper
          </Link>
          <div className="flex gap-2">
            {!solution ? (
              <button
                onClick={regenerate}
                disabled={regenerating}
                className="qp-btn qp-btn-primary"
                data-testid="generate-solution-button"
              >
                <CheckCircle size={16} weight="bold" />
                {regenerating ? "Generating..." : "Generate Solution"}
              </button>
            ) : editing ? (
              <>
                <button
                  onClick={cancelEdit}
                  className="qp-btn qp-btn-secondary"
                  data-testid="cancel-solution-edit-button"
                >
                  <X size={16} weight="bold" /> Cancel
                </button>
                <button
                  onClick={saveEdit}
                  disabled={saving}
                  className="qp-btn qp-btn-primary"
                  data-testid="save-solution-edit-button"
                >
                  <FloppyDisk size={16} weight="bold" />
                  {saving ? "Saving..." : "Save"}
                </button>
              </>
            ) : (
              <>
                <button
                  onClick={regenerate}
                  disabled={regenerating}
                  className="qp-btn qp-btn-secondary"
                  data-testid="regenerate-solution-button"
                >
                  {regenerating ? "Regenerating..." : "Regenerate"}
                </button>
                <button
                  onClick={enterEdit}
                  className="qp-btn qp-btn-secondary"
                  data-testid="enter-solution-edit-button"
                >
                  <PencilSimple size={16} weight="bold" /> Edit
                </button>
                <DropdownMenu>
                  <DropdownMenuTrigger asChild>
                    <button className="qp-btn qp-btn-primary" data-testid="download-solution-menu-button">
                      <Download size={16} weight="bold" /> Download <CaretDown size={14} weight="bold" aria-hidden="true" />
                    </button>
                  </DropdownMenuTrigger>
                  <DropdownMenuContent align="end" className="z-50 min-w-36 rounded-none border-2 border-black bg-white p-1 shadow-none">
                    <DropdownMenuItem onSelect={download} className="cursor-pointer rounded-none px-3 py-2 font-bold focus:bg-neutral-100" data-testid="download-solution-pdf-button">
                      <Download size={16} weight="bold" /> PDF
                    </DropdownMenuItem>
                    <DropdownMenuItem onSelect={downloadDocx} disabled={docxDownloading} className="cursor-pointer rounded-none px-3 py-2 font-bold focus:bg-neutral-100" data-testid="download-solution-docx-button">
                      <Download size={16} weight="bold" /> {docxDownloading ? "Preparing..." : "DOCX"}
                    </DropdownMenuItem>
                  </DropdownMenuContent>
                </DropdownMenu>
              </>
            )}
          </div>
        </div>

        {/* Empty state */}
        {!solution && (
          <div
            className="bg-white border-2 border-black p-10 md:p-16 text-center hard-shadow-static"
            data-testid="solution-empty-state"
          >
            <div className="overline text-neutral-500 mb-3">// ANSWER KEY</div>
            <h1 className="font-display text-4xl md:text-5xl mb-3">
              No solution yet.
            </h1>
            <p className="text-neutral-600 max-w-md mx-auto mb-6">
              Generate an AI answer key for <b>{paper.title}</b> so you can
              verify student responses. Answers are tailored per question type:
              concise for Information, explained for Concept, step-by-step for
              Application.
            </p>
            <button
              onClick={regenerate}
              disabled={regenerating}
              className="qp-btn qp-btn-primary"
              data-testid="generate-solution-cta"
            >
              <CheckCircle size={16} weight="bold" />
              {regenerating ? "Generating..." : "Generate Solution"}
            </button>
          </div>
        )}

        {/* Solution body */}
        {solution && (
          <div
            className="bg-white border-2 border-black p-8 md:p-12 hard-shadow-static-lg"
            data-testid="solution-body"
          >
            {paper.branding_watermark && <div className="paper-branding-watermark text-center text-xs uppercase tracking-[0.35em] text-neutral-300 mb-2" aria-hidden="true">{paper.branding_watermark}</div>}
            <div className="text-center border-b-2 border-black pb-6 mb-6">
              <BrandingLogo paperId={id} hasLogo={Boolean(paper.header_image_path)} className="mx-auto mb-3 max-h-24 max-w-32 object-contain" />
              {paper.school_name && <div className="font-bold text-lg uppercase tracking-wide mb-2">{paper.school_name}</div>}
              <div className="overline text-neutral-500 mb-2">
                // ANSWER KEY
              </div>
              <h1 className="font-display text-3xl md:text-4xl">
                {paper.title}
              </h1>
              <div className="mt-3 font-mono text-sm text-neutral-700">
                Class <b>{paper.class_name}</b> · Subject{" "}
                <b>{paper.subject}</b> · Total Marks{" "}
                <b>{formatMark(paper.total_marks)}</b>
              </div>
              {paper.branding_tagline && <div className="mt-2 text-sm italic text-neutral-500">{paper.branding_tagline}</div>}
            </div>

            {solution.status === "generating" && (
              <div
                className="mb-6 border-2 border-[#002FA7] bg-blue-50 p-4 flex items-start gap-3"
                data-testid="generating-banner"
              >
                <CheckCircle
                  size={24}
                  weight="fill"
                  color="#002FA7"
                  className="shrink-0 animate-pulse"
                />
                <div className="flex-1">
                  <div className="font-bold uppercase tracking-wider text-sm">
                    Generating answers...
                  </div>
                  <div className="text-sm text-neutral-700 mt-1 font-mono">
                    {solution.completed || 0} of {solution.total || 0} answered
                  </div>
                </div>
              </div>
            )}

            {missingQids.length > 0 && !isGenerating && (
              <div
                className="mb-6 border-2 border-[#FFC300] bg-yellow-50 p-4 flex items-start gap-3"
                data-testid="partial-banner"
              >
                <WarningCircle
                  size={24}
                  weight="fill"
                  color="#FFC300"
                  className="shrink-0"
                />
                <div className="flex-1">
                  <div className="font-bold uppercase tracking-wider text-sm">
                    {missingQids.length} answer
                    {missingQids.length === 1 ? "" : "s"} missing
                  </div>
                  <div className="text-sm text-neutral-700 mt-1 font-mono">
                    {allQuestions.length - missingQids.length} of{" "}
                    {allQuestions.length} answered. Fill in the gaps without
                    touching the answers you already have.
                  </div>
                </div>
                <div className="flex flex-col gap-2 shrink-0">
                  <button
                    onClick={() => generateMissing(null)}
                    disabled={busy}
                    className="qp-btn qp-btn-primary text-xs"
                    data-testid="generate-missing-button"
                  >
                    {filling === "all"
                      ? "Starting..."
                      : `Generate ${missingQids.length} missing`}
                  </button>
                  <button
                    onClick={regenerate}
                    disabled={busy}
                    className="qp-btn qp-btn-secondary text-xs"
                    data-testid="partial-regenerate-button"
                  >
                    Regenerate all
                  </button>
                </div>
              </div>
            )}

            {solution.status === "failed" && (
              <div
                className="mb-6 border-2 border-[#E63946] bg-red-50 p-4"
                data-testid="failed-banner"
              >
                <div className="font-bold uppercase tracking-wider text-sm">
                  Generation failed
                </div>
                <div className="text-sm text-neutral-700 mt-1 font-mono">
                  {solution.error || "Try again later."}
                </div>
              </div>
            )}

            {solution.is_stale && (
              <div
                className="mb-6 border-2 border-[#E63946] bg-red-50 p-4 flex items-start gap-3"
                data-testid="stale-banner"
              >
                <WarningCircle
                  size={24}
                  weight="fill"
                  color="#E63946"
                  className="shrink-0"
                />
                <div className="flex-1">
                  <div className="font-bold uppercase tracking-wider text-sm">
                    Solution out of date
                  </div>
                  <div className="text-sm text-neutral-700 mt-1">
                    The question paper has been edited after this solution was
                    generated. Regenerate to refresh answers.
                  </div>
                </div>
                <button
                  onClick={regenerate}
                  disabled={regenerating}
                  className="qp-btn qp-btn-primary text-xs no-print"
                  data-testid="stale-regenerate-button"
                >
                  {regenerating ? "..." : "Regenerate"}
                </button>
              </div>
            )}

            {(paper.sections || []).map((section, si) => (
              <section key={si} className="mb-8">
                <h2 className="font-display text-xl md:text-2xl text-[#002FA7] mb-4 border-b border-neutral-300 pb-2">
                  {section.title}
                </h2>
                <ol className="space-y-6">
                  {(section.questions || []).map((q, qi) => {
                    const qNum = counter++;
                    const solSection = solution.sections?.[si];
                    const aIdx = (solSection?.answers || []).findIndex(
                      (a) => a.question_id === q.id
                    );
                    return (
                      <li
                        key={q.id}
                        className="border-b border-dashed border-neutral-200 pb-6 last:border-0"
                        data-testid={`solution-item-${q.id}`}
                      >
                        <div className="flex items-start gap-2">
                          <span className="font-bold">Q{qNum}.</span>
                          <div className="flex-1 min-w-0">
                            <MatchTheFollowing text={q.question} />
                          </div>
                          <span className="font-mono font-bold text-sm shrink-0">
                            [{formatMark(q.marks)}]
                          </span>
                        </div>
                        <div className="no-print mt-2 flex flex-wrap gap-1 pl-6">
                          <span className={typeBadge(canonicalQuestionType(q.type))}>{questionTypeLabel(q.type)}</span>
                          <span className="qp-badge">{q.difficulty}</span>
                          {q.diagram_path && (
                            <span className="qp-badge">
                              <ImageIcon size={10} weight="bold" /> diagram
                            </span>
                          )}
                        </div>

                        <QuestionDiagram paperId={id} question={q} />

                        <div className="mt-4 pl-6 min-w-0">
                          <div className="overline text-neutral-500 mb-2">
                            // ANSWER
                          </div>
                          {editing && solSection && aIdx >= 0 ? (
                            <textarea
                              value={
                                draft.sections[si].answers[aIdx].answer || ""
                              }
                              onChange={(e) =>
                                updateAnswer(si, aIdx, e.target.value)
                              }
                              className="qp-input w-full font-mono text-sm"
                              rows={Math.max(3, Math.min(12,
                                Math.ceil((draft.sections[si].answers[aIdx].answer || "").length / 80) + 1
                              ))}
                              spellCheck={false}
                              data-testid={`edit-answer-${q.id}`}
                            />
                          ) : (
                            <div
                              className="bg-neutral-50 border border-neutral-300 p-4 whitespace-pre-wrap text-sm leading-relaxed max-w-full"
                              data-testid={`answer-${q.id}`}
                            >
                              {answerByQid[q.id] ? (
                                <MathText text={answerByQid[q.id]} />
                              ) : (
                                <div className="flex items-center justify-between gap-3 flex-wrap">
                                  <span className="text-neutral-400 italic">
                                    {isGenerating ? "(generating...)" : "(no answer)"}
                                  </span>
                                  {paper.solution && (
                                    <button
                                      onClick={() => generateMissing([q.id])}
                                      disabled={busy}
                                      className="qp-btn qp-btn-secondary text-xs no-print"
                                      data-testid={`retry-answer-${q.id}`}
                                    >
                                      {filling === q.id
                                        ? "Starting..."
                                        : "Generate this answer"}
                                    </button>
                                  )}
                                </div>
                              )}
                            </div>
                          )}
                        </div>
                      </li>
                    );
                  })}
                </ol>
              </section>
            ))}

            <div className="text-center mt-8 pt-4 border-t-2 border-black font-mono text-xs text-neutral-500">
              — End of Answer Key —
            </div>
            {paper.branding_footer && <div className="paper-branding-footer text-center mt-3 font-mono text-xs">{paper.branding_footer}</div>}
          </div>
        )}
      </main>
    </div>
  );
}
