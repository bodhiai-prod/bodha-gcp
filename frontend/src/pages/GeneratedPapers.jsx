import React, { useEffect, useMemo, useState } from "react";
import { Link } from "react-router-dom";
import { api } from "@/lib/api";
import Header from "@/components/Header";
import {
  FileText,
  ArrowRight,
  MagnifyingGlass,
  Plus,
  Trash,
} from "@phosphor-icons/react";
import { toast } from "sonner";

export default function GeneratedPapers() {
  const [papers, setPapers] = useState([]);
  const [loading, setLoading] = useState(true);
  const [q, setQ] = useState("");
  const [subjectFilter, setSubjectFilter] = useState("");

  useEffect(() => {
    api
      .get("/papers")
      .then((r) => setPapers(r.data))
      .catch(() => {})
      .finally(() => setLoading(false));
  }, []);

  const subjects = useMemo(() => {
    const s = new Set(papers.map((p) => p.subject).filter(Boolean));
    return Array.from(s).sort();
  }, [papers]);

  const filtered = useMemo(() => {
    const needle = q.trim().toLowerCase();
    return papers.filter((p) => {
      if (subjectFilter && p.subject !== subjectFilter) return false;
      if (!needle) return true;
      return (
        (p.title || "").toLowerCase().includes(needle) ||
        (p.subject || "").toLowerCase().includes(needle)
      );
    });
  }, [papers, q, subjectFilter]);

  const deletePaper = async (paper) => {
    if (!window.confirm(`Delete "${paper.title || "this paper"}"? This cannot be undone.`)) {
      return;
    }
    try {
      await api.delete(`/papers/${paper.id}`);
      setPapers((current) => current.filter((item) => item.id !== paper.id));
      toast.success("Paper deleted");
    } catch (err) {
      toast.error(err?.response?.data?.detail || "Could not delete paper");
    }
  };

  return (
    <div className="min-h-screen bg-[#FAFAFA]">
      <Header />
      <main
        className="max-w-7xl mx-auto p-6 md:p-12"
        data-testid="generated-papers-page"
      >
        <div className="overline text-neutral-500 mb-3">
          // ALL GENERATED PAPERS ({papers.length})
        </div>
        <div className="flex flex-col md:flex-row md:items-end md:justify-between gap-6 mb-10">
          <h1 className="font-display text-5xl md:text-6xl leading-[0.9]">
            Generated
            <br />
            <span className="text-[#002FA7]">papers.</span>
          </h1>
          <Link
            to="/papers/new"
            data-testid="generated-papers-new"
            className="qp-btn qp-btn-primary text-base py-3 px-5 self-start md:self-end"
          >
            <Plus size={18} weight="bold" />
            Generate New Paper
          </Link>
        </div>

        {/* Search + filter */}
        <div className="grid grid-cols-1 md:grid-cols-3 gap-3 mb-6">
          <div className="md:col-span-2 relative">
            <MagnifyingGlass
              size={18}
              weight="bold"
              className="absolute left-3 top-1/2 -translate-y-1/2 text-neutral-500"
            />
            <input
              value={q}
              onChange={(e) => setQ(e.target.value)}
              placeholder="Search by title or subject..."
              className="qp-input pl-10"
              data-testid="generated-papers-search"
            />
          </div>
          <select
            value={subjectFilter}
            onChange={(e) => setSubjectFilter(e.target.value)}
            className="qp-input"
            data-testid="generated-papers-subject-filter"
          >
            <option value="">All subjects</option>
            {subjects.map((s) => (
              <option key={s} value={s}>
                {s}
              </option>
            ))}
          </select>
        </div>

        <div className="border-2 border-black bg-white">
          {loading ? (
            <div className="p-8 text-neutral-500">Loading...</div>
          ) : papers.length === 0 ? (
            <div
              className="p-10 text-center text-neutral-500"
              data-testid="generated-papers-empty"
            >
              No papers yet. Generate your first paper to see it here.
            </div>
          ) : filtered.length === 0 ? (
            <div
              className="p-10 text-center text-neutral-500"
              data-testid="generated-papers-no-results"
            >
              No papers match your search.
            </div>
          ) : (
            filtered.map((p, idx) => (
              <div
                key={p.id}
                data-testid={`generated-paper-row-${idx}`}
                className={`flex items-center justify-between gap-4 p-4 hover:bg-neutral-50 ${
                  idx !== filtered.length - 1 ? "border-b border-neutral-200" : ""
                }`}
              >
                <Link to={`/papers/${p.id}`} className="flex items-center gap-4 min-w-0 flex-1">
                  <div className="w-10 h-10 border-2 border-black flex items-center justify-center shrink-0">
                    <FileText size={18} weight="bold" />
                  </div>
                  <div className="min-w-0">
                    <div className="font-bold truncate">{p.title}</div>
                    <div className="text-xs text-neutral-500 font-mono mt-0.5">
                      {p.subject} · Class {p.class_name} · {p.total_marks}{" "}
                      marks · {p.duration_minutes} min
                      {p.created_at
                        ? ` · ${new Date(p.created_at).toLocaleDateString()}`
                        : ""}
                    </div>
                  </div>
                  <ArrowRight size={18} weight="bold" className="shrink-0" />
                </Link>
                <button
                  type="button"
                  onClick={() => deletePaper(p)}
                  className="qp-btn qp-btn-secondary text-xs shrink-0"
                  aria-label={`Delete ${p.title || "paper"}`}
                  data-testid={`generated-paper-delete-${p.id}`}
                >
                  <Trash size={16} weight="bold" />
                  <span className="hidden sm:inline">Delete</span>
                </button>
              </div>
            ))
          )}
        </div>

        {!loading && papers.length > 0 && (
          <div
            className="text-xs text-neutral-500 font-mono mt-3"
            data-testid="generated-papers-count"
          >
            Showing {filtered.length} of {papers.length} papers
          </div>
        )}
      </main>
    </div>
  );
}
