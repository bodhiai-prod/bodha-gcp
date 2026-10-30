import React, { useEffect, useState } from "react";
import { api } from "@/lib/api";
import Header from "@/components/Header";
import { toast } from "sonner";
import { useConfirmation } from "@/context/ConfirmationContext";
import {
  UploadSimple,
  FolderOpen,
  Sparkle,
  Trash,
  CheckCircle,
  CaretRight,
  CaretDown,
} from "@phosphor-icons/react";

const ACTIVE_STATUSES = new Set(["queued", "ingesting", "topics_extracting"]);
const ACTIVE_BOOKS_KEY = "bodha.activeTextbookIds";
const isTopicExtractionActive = (book) => ["queued", "running"].includes(book.topic_extraction?.status);
const isBookActive = (book) => ACTIVE_STATUSES.has(book.status) || isTopicExtractionActive(book);

const rememberActiveBooks = (books) => {
  const ids = books.filter(isBookActive).map((book) => book.id);
  window.localStorage.setItem(ACTIVE_BOOKS_KEY, JSON.stringify(ids));
};

export default function Textbooks() {
  const confirm = useConfirmation();
  const [textbooks, setTextbooks] = useState([]);
  const [subject, setSubject] = useState("");
  const [className, setClassName] = useState("");
  const [file, setFile] = useState(null);
  const [dragActive, setDragActive] = useState(false);
  const [uploading, setUploading] = useState(false);
  const [expanded, setExpanded] = useState({});
  const [detailTopics, setDetailTopics] = useState({}); // id -> topics

  const load = async () => {
    const { data } = await api.get("/textbooks");
    setTextbooks(data);
    rememberActiveBooks(data);
  };

  useEffect(() => {
    load();
  }, []);

  // Ingestion belongs to the backend and continues across navigation. While
  // this page is visible, refresh pending rows so completion appears without a
  // manual reload. The interval is removed automatically on navigation.
  useEffect(() => {
    if (!textbooks.some(isBookActive)) return undefined;
    const interval = window.setInterval(() => {
      load().catch(() => {});
    }, 3000);
    return () => window.clearInterval(interval);
  }, [textbooks]); // eslint-disable-line react-hooks/exhaustive-deps

  const onFile = (f) => {
    if (!f) return;
    if (!f.name.toLowerCase().endsWith(".pdf")) {
      toast.error("Only PDF files are accepted");
      return;
    }
    setFile(f);
  };

  const onDrop = (e) => {
    e.preventDefault();
    setDragActive(false);
    onFile(e.dataTransfer.files?.[0]);
  };

  const upload = async (e) => {
    e.preventDefault();
    if (!file) return toast.error("Please select a PDF");
    if (!subject || !className) return toast.error("Subject & Class required");
    const fd = new FormData();
    fd.append("file", file);
    setUploading(true);
    try {
      const uploadUrl = `/textbooks/upload?subject=${encodeURIComponent(
        subject
      )}&class_name=${encodeURIComponent(className)}`;
      const uploadFile = (allowDuplicate = false) =>
        api.post(
          `${uploadUrl}&allow_duplicate=${allowDuplicate}`,
          fd,
          { headers: { "Content-Type": "multipart/form-data" } }
        );

      let response;
      try {
        response = await uploadFile();
      } catch (err) {
        const detail = err?.response?.data?.detail;
        if (
          err?.response?.status !== 409 ||
          detail?.code !== "duplicate_textbook"
        ) {
          throw err;
        }

        const existingName = detail.existing_filename || file.name;
        const confirmed = await confirm({
          title: "Upload duplicate textbook?",
          description: `A book named "${existingName}" already exists in your library. Is this the same book, and do you still want to upload it again?`,
          confirmLabel: "Upload anyway",
        });
        if (!confirmed) {
          toast.message("Duplicate upload cancelled");
          return;
        }
        response = await uploadFile(true);
      }
      const active = JSON.parse(window.localStorage.getItem(ACTIVE_BOOKS_KEY) || "[]");
      window.localStorage.setItem(ACTIVE_BOOKS_KEY, JSON.stringify([...new Set([...active, response.data.id])]));
      toast.success("Upload accepted — ingestion is continuing in the background");
      setFile(null);
      setSubject("");
      setClassName("");
      await load();
    } catch (err) {
      toast.error(err?.response?.data?.detail || "Upload failed");
    } finally {
      setUploading(false);
    }
  };

  const extractTopics = async (id) => {
    try {
      const { data } = await api.post(`/textbooks/${id}/extract-topics?background=true`);
      if (data.status !== "running") {
        setDetailTopics({ ...detailTopics, [id]: data.topics });
        setExpanded({ ...expanded, [id]: true });
        toast.success("Topics extracted");
      }
      await load();
    } catch (err) {
      toast.error(err?.response?.data?.detail || "Extraction failed");
    }
  };

  const deleteTextbook = async (id) => {
    if (!(await confirm({
      title: "Delete textbook?",
      description: "This textbook and its extracted content will be removed.",
      confirmLabel: "Delete textbook",
      tone: "danger",
    }))) return;
    await api.delete(`/textbooks/${id}`);
    toast.success("Deleted");
    await load();
  };

  const toggleExpand = async (tb) => {
    const nextOpen = !expanded[tb.id];
    setExpanded({ ...expanded, [tb.id]: nextOpen });
    if (nextOpen && !detailTopics[tb.id]) {
      const { data } = await api.get(`/textbooks/${tb.id}`);
      setDetailTopics({ ...detailTopics, [tb.id]: data.topics || [] });
    }
  };

  return (
    <div className="min-h-screen bg-[#FAFAFA]">
      <Header />
      <main className="max-w-7xl mx-auto p-6 md:p-12" data-testid="textbooks-page">
        <div className="overline text-neutral-500 mb-3">// TEXTBOOK INDEX</div>
        <h1 className="font-display text-5xl md:text-6xl mb-10">
          Textbooks &<br />
          <span className="text-[#002FA7]">Topic Extraction.</span>
        </h1>

        <div className="grid grid-cols-1 lg:grid-cols-5 gap-6 md:gap-8 min-w-0">
          {/* Upload */}
          <form
            onSubmit={upload}
            className="lg:col-span-2 qp-card hard-shadow-static"
            data-testid="textbook-upload-form"
          >
            <div className="overline text-neutral-500 mb-3">// NEW UPLOAD</div>
            <h2 className="font-display text-3xl mb-6">Upload PDF</h2>

            <div className="mb-4">
              <label className="qp-label">Subject</label>
              <input
                value={subject}
                onChange={(e) => setSubject(e.target.value)}
                placeholder="e.g., Physics"
                className="qp-input"
                data-testid="upload-subject-input"
                required
              />
            </div>
            <div className="mb-4">
              <label className="qp-label">Class</label>
              <input
                value={className}
                onChange={(e) => setClassName(e.target.value)}
                placeholder="e.g., 10"
                className="qp-input"
                data-testid="upload-class-input"
                required
              />
            </div>

            <label
              onDragOver={(e) => {
                e.preventDefault();
                setDragActive(true);
              }}
              onDragLeave={() => setDragActive(false)}
              onDrop={onDrop}
              className={`qp-drop mt-2 ${dragActive ? "active" : ""}`}
              data-testid="upload-drop-zone"
            >
              <UploadSimple size={32} weight="bold" />
              <div
                className="mt-3 max-w-full break-all text-center text-sm font-bold uppercase tracking-wider line-clamp-2"
                title={file?.name}
                aria-label={file ? `Selected PDF: ${file.name}` : undefined}
              >
                {file ? file.name : "Drop PDF or click to browse"}
              </div>
              <div className="text-xs text-neutral-500 font-mono mt-1">
                Max 500MB · Textbook chapters recommended
              </div>
              <input
                type="file"
                accept="application/pdf"
                onChange={(e) => onFile(e.target.files?.[0])}
                className="hidden"
                data-testid="upload-file-input"
              />
            </label>

            <button
              type="submit"
              disabled={uploading}
              className="qp-btn qp-btn-primary w-full mt-6"
              data-testid="upload-submit-button"
            >
              {uploading ? "Uploading & indexing..." : "Upload & Index"}
            </button>
          </form>

          {/* List */}
          <div className="lg:col-span-3 min-w-0">
            <div className="overline text-neutral-500 mb-3">
              // INDEXED ({textbooks.length})
            </div>
            {textbooks.length === 0 ? (
              <div className="border-2 border-black bg-white p-10 text-center">
                <FolderOpen size={40} weight="duotone" className="mx-auto" />
                <div className="mt-3 font-display text-2xl">
                  No textbooks yet
                </div>
                <div className="text-sm text-neutral-500 mt-1">
                  Upload your first textbook to start generating papers.
                </div>
              </div>
            ) : (
              <div className="textbook-library-scroll">
                {textbooks.map((tb) => (
                  <div
                    key={tb.id}
                    className="border-b-2 border-black bg-white last:border-b-0"
                    data-testid={`textbook-${tb.id}`}
                  >
                    <div className="grid min-h-[84px] gap-x-4 gap-y-3 p-4 sm:grid-cols-[auto_minmax(0,1fr)_auto] sm:items-center xl:grid-cols-[auto_minmax(0,1fr)_auto_auto]">
                      <button
                        onClick={() => toggleExpand(tb)}
                        className="self-start pt-0.5 text-left sm:self-center"
                        data-testid={`textbook-toggle-${tb.id}`}
                        aria-label={`${expanded[tb.id] ? "Collapse" : "Expand"} ${tb.original_filename}`}
                      >
                        {expanded[tb.id] ? (
                          <CaretDown size={20} weight="bold" />
                        ) : (
                          <CaretRight size={20} weight="bold" />
                        )}
                      </button>
                      <button
                        onClick={() => toggleExpand(tb)}
                        className="min-w-0 self-start text-left sm:self-center"
                        data-testid={`textbook-title-${tb.id}`}
                        title={`View topics for ${tb.original_filename}`}
                      >
                        <div className="flex-1 min-w-0">
                          <div
                            className="textbook-filename font-bold leading-snug"
                            title={tb.original_filename}
                          >
                            {tb.original_filename}
                          </div>
                          <div className="mt-0.5 text-xs font-mono text-neutral-500">
                            {tb.subject} · Class {tb.class_name}
                            {tb.is_shared && " · Shared library"}
                          </div>
                        </div>
                      </button>
                      <div className="col-span-full flex min-w-0 flex-wrap items-center gap-1 sm:col-span-1 sm:justify-self-end sm:flex-col sm:items-end xl:col-span-1">
                        <span
                          className={`qp-badge whitespace-nowrap ${
                            tb.status === "topics_ready"
                              ? "qp-badge-success"
                              : tb.status === "indexed"
                              ? "qp-badge-blue"
                              : "qp-badge-red"
                          }`}
                        >
                          {tb.status.replace("_", " ")}
                        </span>
                        {tb.topic_count > 0 && (
                          <span className="qp-badge whitespace-nowrap">
                            {tb.topic_count} topics
                          </span>
                        )}
                      </div>
                      <div className="col-span-full flex flex-wrap gap-2 sm:col-span-3 sm:justify-self-end xl:col-span-1 xl:flex-nowrap xl:items-center">
                        <button
                          onClick={() => extractTopics(tb.id)}
                          disabled={isTopicExtractionActive(tb) || tb.is_owned === false}
                          className="qp-btn qp-btn-secondary min-w-36 flex-1 justify-center whitespace-nowrap text-xs sm:flex-none"
                          title={tb.is_owned === false ? "Shared library — read-only" : undefined}
                          data-testid={`extract-topics-${tb.id}`}
                        >
                          <Sparkle size={14} weight="bold" />
                          {tb.topic_extraction?.status === "queued"
                            ? "Queued"
                            : tb.topic_extraction?.status === "running"
                            ? "Analyzing..."
                            : tb.topic_count > 0
                            ? "Re-extract"
                            : "Extract topics"}
                        </button>
                        <button
                          onClick={() => deleteTextbook(tb.id)}
                          disabled={tb.is_owned === false}
                          className="qp-btn qp-btn-danger min-h-10 flex-1 justify-center whitespace-nowrap text-xs sm:flex-none"
                          aria-label="Delete textbook"
                          title={tb.is_owned === false ? "Shared library — read-only" : undefined}
                          data-testid={`delete-textbook-${tb.id}`}
                        >
                          <Trash size={16} weight="bold" />
                          Delete
                        </button>
                      </div>
                    </div>

                    {expanded[tb.id] && (
                      <div className="border-t border-neutral-200 p-4 bg-neutral-50">
                        {detailTopics[tb.id]?.length ? (
                          <div className="font-mono text-sm space-y-2">
                            {detailTopics[tb.id].map((t, i) => (
                              <div
                                key={i}
                                data-testid={`topic-${tb.id}-${i}`}
                              >
                                <div className="flex items-center gap-2 font-bold">
                                  <CheckCircle
                                    size={14}
                                    weight="fill"
                                    color="#002FA7"
                                  />
                                  {t.name}
                                </div>
                                {t.subtopics?.length > 0 && (
                                  <ul className="pl-7 text-neutral-600">
                                    {t.subtopics.map((s, j) => (
                                      <li key={j}>— {s}</li>
                                    ))}
                                  </ul>
                                )}
                              </div>
                            ))}
                          </div>
                        ) : (
                          <div className="text-sm text-neutral-500">
                            No topics extracted yet. Click{" "}
                            <b>Extract topics</b> to generate.
                          </div>
                        )}
                      </div>
                    )}
                  </div>
                ))}
              </div>
            )}
          </div>
        </div>
      </main>
    </div>
  );
}
