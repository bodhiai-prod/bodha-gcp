import React, { useEffect, useMemo, useState, useRef } from "react";
import { Link } from "react-router-dom";
import { api } from "@/lib/api";
import Header from "@/components/Header";
import PaperStatusBadge from "@/components/PaperStatusBadge";
import {
  FileText,
  ArrowRight,
  MagnifyingGlass,
  Plus,
  Trash,
} from "@phosphor-icons/react";
import { toast } from "sonner";
import { useConfirmation } from "@/context/ConfirmationContext";

export default function GeneratedPapers() {
  const confirm = useConfirmation();
  const [papers, setPapers] = useState([]);
  const [loading, setLoading] = useState(true);
  const [q, setQ] = useState("");
  const [subjectFilter, setSubjectFilter] = useState("");
  const [page, setPage] = useState(1);
  const papersPerPage = 15;

  const requestSequence = useRef(0);
  const loadPapers = () => {
    const sequence = ++requestSequence.current;
    return api.get("/papers").then(r => {
      if (sequence === requestSequence.current) setPapers(r.data);
    });
  };
  useEffect(() => {
    loadPapers()
      .catch(() => {})
      .finally(() => setLoading(false));
  }, []);

  useEffect(() => {
    if (!papers.some((paper) => paper.library_status?.active)) return undefined;
    const interval = window.setInterval(() => {
      loadPapers().catch(() => {});
    }, 3000);
    return () => window.clearInterval(interval);
  }, [papers]);

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
    }).sort((a, b) => {
      const aCreated = a.created_at ? new Date(a.created_at).getTime() : 0;
      const bCreated = b.created_at ? new Date(b.created_at).getTime() : 0;
      return bCreated - aCreated;
    });
  }, [papers, q, subjectFilter]);

  const totalPages = Math.max(1, Math.ceil(filtered.length / papersPerPage));
  const visiblePapers = filtered.slice((page - 1) * papersPerPage, page * papersPerPage);

  useEffect(() => {
    setPage((current) => Math.min(current, totalPages));
  }, [totalPages]);

  const deletePaper = async (paper) => {
    if (!(await confirm({
      title: "Delete paper?",
      description: `Delete "${paper.title || "this paper"}"? This cannot be undone.`,
      confirmLabel: "Delete paper",
      tone: "danger",
    }))) {
      return;
    }
    try {
      await api.delete(`/papers/${paper.id}`);
      ++requestSequence.current;
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
              onChange={(e) => {
                setQ(e.target.value);
                setPage(1);
              }}
              placeholder="Search by title or subject..."
              className="qp-input !pl-11"
              data-testid="generated-papers-search"
            />
          </div>
          <select
            value={subjectFilter}
            onChange={(e) => {
              setSubjectFilter(e.target.value);
              setPage(1);
            }}
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
            visiblePapers.map((p, idx) => (
              <div
                key={p.id}
                data-testid={`generated-paper-row-${idx}`}
                className={`paper-list-row ${
                  idx !== visiblePapers.length - 1 ? "border-b border-neutral-200" : ""
                }`}
              >
                <FileText size={18} weight="fill" className="paper-list-icon" aria-hidden="true" />
                <Link to={`/papers/${p.id}`} className="paper-list-link">
                  <div className="paper-list-title">{p.title}</div>
                  <div className="paper-list-detail">
                    <PaperStatusBadge status={p.library_status} testid={`generated-paper-status-${p.id}`} />
                    <span>
                      {p.subject} · Class {p.class_name} · {p.total_marks}{" "}
                      marks · {p.duration_minutes} min
                      {p.created_at
                        ? ` · ${new Date(p.created_at).toLocaleDateString()}`
                        : ""}
                    </span>
                  </div>
                </Link>
                <Link to={`/papers/${p.id}`} className="paper-list-open" aria-label={`Open ${p.title || "paper"}`}>
                  <ArrowRight size={28} weight="regular" aria-hidden="true" />
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

        {!loading && papers.length > 0 && filtered.length > 0 && (
          <div className="mt-4 flex flex-col gap-3 text-xs font-mono text-neutral-500 sm:flex-row sm:items-center sm:justify-between">
            <div data-testid="generated-papers-count">
              Showing {(page - 1) * papersPerPage + 1}–{Math.min(page * papersPerPage, filtered.length)} of {filtered.length} papers
              {filtered.length !== papers.length ? ` (${papers.length} total)` : ""}
            </div>
            <div className="flex items-center gap-2" aria-label="Paper pagination">
              <button
                type="button"
                onClick={() => setPage((current) => Math.max(1, current - 1))}
                disabled={page === 1}
                className="qp-btn qp-btn-secondary !px-3 !py-1.5 text-xs"
                data-testid="generated-papers-previous"
              >
                Previous
              </button>
              <span className="min-w-20 text-center">Page {page} of {totalPages}</span>
              <button
                type="button"
                onClick={() => setPage((current) => Math.min(totalPages, current + 1))}
                disabled={page === totalPages}
                className="qp-btn qp-btn-secondary !px-3 !py-1.5 text-xs"
                data-testid="generated-papers-next"
              >
                Next
              </button>
            </div>
          </div>
        )}
      </main>
    </div>
  );
}
