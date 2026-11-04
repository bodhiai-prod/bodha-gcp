import React, { useEffect, useMemo, useState } from "react";
import { flushSync } from "react-dom";
import { useNavigate } from "react-router-dom";
import { api } from "@/lib/api";
import { BLUEPRINT_PRESETS, fillBlueprint } from "@/lib/blueprints";
import { buildTopicPayload, topicKey } from "@/lib/topicDifficulty";
import { apportionCounts } from "@/lib/questionCounts";
import Header from "@/components/Header";
import SectionBlueprintEditor from "@/components/SectionBlueprintEditor";
import FormatTopicAllocator, { allocationStatus } from "@/components/FormatTopicAllocator";
import ConfigurationSummary from "@/components/ConfigurationSummary";
import SavedPaperConfigurations from "@/components/SavedPaperConfigurations";
import TextbookMultiSelect from "@/components/TextbookMultiSelect";
import { normalizeBucketAllocations, retainCompatibleAllocations } from "@/lib/sectionBlueprint";
import { countDiagramSelections, MAX_DIAGRAMS_PER_PAPER } from "@/lib/diagramSelection";
import useConfigurationPreview from "@/hooks/useConfigurationPreview";
import { toast } from "sonner";
import {
  Sparkle,
  ArrowRight,
  CheckSquare,
  MinusSquare,
  Square,
  X as XIcon,
  Plus,
  CaretDown,
} from "@phosphor-icons/react";

const DIFFICULTIES = ["easy", "medium", "hard"];
const DIFFICULTY_LABELS = { easy: "Simple", medium: "Moderate", hard: "Challenging" };
const isTopicExtractionActive = (textbook) => ["queued", "running"].includes(textbook?.topic_extraction?.status);
const DEFAULT_FORMATS = [
  { key: "mcq", label: "MCQ", default: 0 },
  { key: "short_answer", label: "Short Answer", default: 40 },
  { key: "long_answer", label: "Long Answer", default: 40 },
  { key: "fill_blank", label: "Fill in the Blanks", default: 10 },
  { key: "true_false", label: "True / False", default: 10 },
];

export default function NewPaper() {
  const navigate = useNavigate();
  const [textbooks, setTextbooks] = useState([]);
  const [selectedTbIds, setSelectedTbIds] = useState([]); // list of ids
  const [tbDetails, setTbDetails] = useState({}); // { id: { topics, subject, class_name } }
  const [title, setTitle] = useState("");
  const [difficulty, setDifficulty] = useState("medium");
  const [duration, setDuration] = useState(60);
  const [totalMarks, setTotalMarks] = useState(50);
  const [questionCount, setQuestionCount] = useState(16);
  const [diagramCount, setDiagramCount] = useState("");
  const [diagramCountError, setDiagramCountError] = useState("");
  const [info, setInfo] = useState(40);
  const [concept, setConcept] = useState(40);
  const [selectedTopics, setSelectedTopics] = useState([]);
  const [expandedTopics, setExpandedTopics] = useState({});
  // Scoped by book and parent so similarly named subtopics stay independent.
  const [topicDifficulties, setTopicDifficulties] = useState({});
  const [generating, setGenerating] = useState(false);
  const [customInstructions, setCustomInstructions] = useState("");
  const [sectionBlueprint, setSectionBlueprint] = useState("");
  const [blueprintCatalog, setBlueprintCatalog] = useState([]);
  const [brandingPresets, setBrandingPresets] = useState([]);
  const [brandingId, setBrandingId] = useState("");
  const [blueprint, setBlueprint] = useState(null);
  const [blueprintFamily, setBlueprintFamily] = useState(null);
  const [allocations, setAllocations] = useState([]);
  const [analyzing, setAnalyzing] = useState(false);
  const [step, setStep] = useState(0);
  // Format mix: array of { key, label, value, custom }
  const [formats, setFormats] = useState(
    DEFAULT_FORMATS.map((f) => ({ ...f, value: f.default, custom: false }))
  );
  const [newFormatLabel, setNewFormatLabel] = useState("");
  const [formatTopicAllocations, setFormatTopicAllocations] = useState([]);
  const [formatTopicStage, setFormatTopicStage] = useState("mix");
  const effectiveQuestionCount = Number(questionCount);
  const formatQuestionCounts = apportionCounts(
    Object.fromEntries(formats.map((f) => [f.key, f.value])),
    effectiveQuestionCount
  );
  const activeFormats = useMemo(() => formats
    .map((format) => ({ ...format, count: Number(formatQuestionCounts[format.key] || 0) }))
    .filter((format) => format.count > 0), [formats, formatQuestionCounts]);
  const formatTopicStatus = useMemo(
    () => allocationStatus(activeFormats, formatTopicAllocations),
    [activeFormats, formatTopicAllocations]
  );
  const assignedFormatTopicCount = useMemo(
    () => formatTopicAllocations.reduce((sum, row) => sum + Number(row.count || 0), 0),
    [formatTopicAllocations]
  );
  const allocatedTopics = useMemo(() => Array.from(new Map(formatTopicAllocations.map((row) => [
    `${row.textbook_id}:${row.topic_id}`,
    { textbook_id: row.textbook_id, topic_id: row.topic_id, name: row.name, parent_topic: row.parent_topic || "", weight: 5 },
  ])).values()), [formatTopicAllocations]);

  const application = useMemo(
    () => Math.max(0, 100 - info - concept),
    [info, concept]
  );
  const typeQuestionCounts = apportionCounts({ information: info, concept, application }, effectiveQuestionCount);

  const formatTotal = useMemo(
    () => formats.reduce((sum, f) => sum + Number(f.value || 0), 0),
    [formats]
  );
  const formatActive = useMemo(
    () => formats.some((f) => Number(f.value || 0) > 0),
    [formats]
  );
  // Saved configurations may still carry one row per old question slot.
  // Normalize at the form boundary as well as inside the editor so previews
  // and generation can never send that stale shape to the backend.
  const blueprintAllocations = useMemo(
    () => blueprint ? normalizeBucketAllocations(allocations, blueprint) : allocations,
    [allocations, blueprint]
  );

  const currentConfiguration = useMemo(() => ({
    difficulty,
    duration_minutes: Number(duration),
    total_marks: Number(totalMarks),
    total_questions: sectionBlueprint.trim() ? null : effectiveQuestionCount,
    diagram_count: diagramCount === "" ? null : Number(diagramCount),
    distribution: { information: Number(info), concept: Number(concept), application: Number(application) },
    format_distribution: Object.fromEntries(formats.filter((f) => Number(f.value) > 0).map((f) => [f.key, Number(f.value)])),
    format_counts: null,
    format_topic_allocations: blueprint ? null : formatTopicAllocations,
    custom_instructions: customInstructions.trim() || null,
    section_blueprint: sectionBlueprint.trim() || null,
    blueprint_id: blueprint?.id || null,
    blueprint_version: blueprint?.version || null,
    section_topic_allocations: blueprint ? blueprintAllocations : null,
    textbook_ids: selectedTbIds,
    topics: blueprint ? selectedTopics.map(({ key, ...topic }) => ({ ...topic, difficulty: topicDifficulties[key] || null })) : allocatedTopics,
  }), [difficulty, duration, totalMarks, effectiveQuestionCount, diagramCount, info, concept, application, formats, formatTopicAllocations, customInstructions, sectionBlueprint, blueprint, blueprintAllocations, selectedTbIds, selectedTopics, topicDifficulties, allocatedTopics]);

  const applySavedConfiguration = (config) => {
    const available = new Set(textbooks.map((book) => book.id));
    const validBookIds = (config.textbook_ids || []).filter((bookId) => available.has(bookId));
    if (validBookIds.length !== (config.textbook_ids || []).length) toast.warning("Some saved textbooks are unavailable and were skipped.");
    setDifficulty(config.difficulty || "medium");
    setDuration(config.duration_minutes || 60);
    setTotalMarks(config.total_marks || 50);
    if (config.total_questions != null) setQuestionCount(config.total_questions);
    const restoredDiagramCount = config.diagram_count == null ? "" : Math.min(MAX_DIAGRAMS_PER_PAPER, Number(config.diagram_count));
    setDiagramCount(restoredDiagramCount);
    setDiagramCountError("");
    if (Number(config.diagram_count) > MAX_DIAGRAMS_PER_PAPER) toast.warning("Saved diagram count was reduced to the maximum of 5.");
    setInfo(Number(config.distribution?.information ?? 40));
    setConcept(Number(config.distribution?.concept ?? 40));
    const savedMix = config.format_distribution || config.format_counts;
    if (savedMix) {
      const defaults = Object.fromEntries(DEFAULT_FORMATS.map((format) => [format.key, format]));
      const restoredFormats = Object.entries(savedMix).map(([key, value]) => ({
        ...(defaults[key] || { key, label: key.replaceAll("_", " "), default: 0 }),
        value: Number(value),
        custom: !defaults[key],
      }));
      setFormats(config.format_counts ? distributeFormatValues(restoredFormats, 100) : restoredFormats);
      if (config.format_counts && config.total_questions == null) {
        setQuestionCount(Object.values(config.format_counts).reduce((sum, value) => sum + Number(value || 0), 0));
      }
    }
    setFormatTopicAllocations(config.format_topic_allocations || []);
    setFormatTopicStage(config.format_topic_allocations?.length ? "confirmed" : "mix");
    setCustomInstructions(config.custom_instructions || "");
    setSectionBlueprint(config.section_blueprint || "");
    setBlueprint(config.blueprint_id ? blueprintCatalog.find((item) => item.id === config.blueprint_id && (!config.blueprint_version || item.version === config.blueprint_version)) || null : null);
    setAllocations(config.section_topic_allocations || []);
    setSelectedTbIds(validBookIds);
    const topics = (config.topics || []).filter((topic) => validBookIds.includes(topic.textbook_id)).map((topic) => ({ ...topic, key: topicKey(topic.textbook_id, topic.name, topic.parent_topic || "") }));
    setSelectedTopics(topics);
    setTopicDifficulties(Object.fromEntries(topics.filter((topic) => topic.difficulty).map((topic) => [topic.key, topic.difficulty])));
    setStep(0);
    toast.success(`Applied ${config.name}. Review the restored sources before generating.`);
  };

  // A Paper Blueprint defines the sections itself, so the slider-based
  // Question Distribution and Format Mix would fight it and are greyed out.
  //
  // Additional Instructions used to grey them too, which is why typing
  // "make Section A hard" silently threw away the format mix the teacher
  // had just set. They compose now: the backend layers the instructions on
  // top of these settings and states which wins where they disagree.
  const promptOverride = useMemo(
    () => Boolean(sectionBlueprint.trim()),
    [sectionBlueprint]
  );

  useEffect(() => {
    api.get("/textbooks").then((r) => setTextbooks(r.data)).catch(() => toast.error("Could not load textbooks. Reload to retry."));
    api.get("/paper-blueprints").then(r => setBlueprintCatalog(r.data)).catch(() => toast.error("Could not load board templates. Reload to retry."));
    api.get("/branding-presets").then(r => setBrandingPresets(r.data)).catch(() => toast.error("Could not load branding presets."));
  }, []);

  // Load detail (topics) for every selected textbook not yet in cache.
  useEffect(() => {
    const missing = selectedTbIds.filter((id) => !tbDetails[id]);
    if (missing.length === 0) return;
    Promise.all(
      missing.map((id) => api.get(`/textbooks/${id}`).then((r) => [id, r.data]))
    ).then((pairs) => {
      setTbDetails((curr) => {
        const next = { ...curr };
        for (const [id, detail] of pairs) next[id] = detail;
        return next;
      });
    });
  }, [selectedTbIds]); // eslint-disable-line

  // Details may become ready while this screen is open (or after extraction
  // was started on the Textbooks screen). Refresh only selected active books
  // so the topic picker cannot remain stuck with an old empty cache.
  useEffect(() => {
    const activeIds = selectedTbIds.filter((id) => {
      const listStatus = textbooks.find((book) => book.id === id)?.status;
      const detailStatus = tbDetails[id]?.topic_extraction?.status;
      return ["queued", "ingesting", "topics_extracting"].includes(listStatus) || detailStatus === "running";
    });
    if (!activeIds.length) return undefined;
    const refresh = async () => {
      const pairs = await Promise.all(activeIds.map(async (id) => [id, (await api.get(`/textbooks/${id}`)).data]));
      setTbDetails((current) => ({ ...current, ...Object.fromEntries(pairs) }));
      setTextbooks((current) => current.map((book) => {
        const detail = Object.fromEntries(pairs)[book.id];
        return detail ? { ...book, status: detail.status, topic_extraction: detail.topic_extraction } : book;
      }));
    };
    const timer = window.setInterval(() => { refresh().catch(() => {}); }, 3000);
    return () => window.clearInterval(timer);
  }, [selectedTbIds, textbooks, tbDetails]); // eslint-disable-line react-hooks/exhaustive-deps

  const setSelectedTextbooks = (nextIds) => {
    const removedIds = new Set(selectedTbIds.filter((id) => !nextIds.includes(id)));
    setSelectedTbIds(nextIds);
    if (!removedIds.size) return;
    setSelectedTopics((curr) => curr.filter((topic) => !removedIds.has(topic.textbook_id)));
    setAllocations((curr) => curr.filter((allocation) => !removedIds.has(allocation.textbook_id)));
    setFormatTopicAllocations((curr) => curr.filter((allocation) => !removedIds.has(allocation.textbook_id)));
  };

  const toggleTopic = (bookId, name, parent = "") => {
    const key = topicKey(bookId, name, parent);
    setSelectedTopics((curr) =>
      curr.find((t) => t.key === key)
        ? curr.filter((t) => t.key !== key)
        : [...curr, { key, name, textbook_id: bookId, parent_topic: parent, weight: 5 }]
    );
  };

  const topicsForBook = (bookId, topics) => topics.flatMap((topic) => [
    { name: topic.name, parent_topic: "" },
    ...(topic.subtopics || []).map((name) => ({ name, parent_topic: topic.name })),
  ]).filter((topic) => topic.name).map((topic) => ({
    ...topic, textbook_id: bookId, weight: 5,
    key: topicKey(bookId, topic.name, topic.parent_topic),
  }));

  const toggleAllTopicsForBook = (bookId, topics) => {
    const items = topicsForBook(bookId, topics);
    const selectedKeys = new Set(selectedTopics.map((topic) => topic.key));
    const allSelected = items.length > 0 && items.every((item) => selectedKeys.has(item.key));

    setSelectedTopics((curr) => {
      if (allSelected) {
        const keysToClear = new Set(items.map((item) => item.key));
        return curr.filter((topic) => !keysToClear.has(topic.key));
      }

      const existing = new Set(curr.map((topic) => topic.key));
      return [
        ...curr,
        ...items.filter((item) => !existing.has(item.key)),
      ];
    });
  };

  const extractTopicsFor = async (id) => {
    try {
      const { data } = await api.post(`/textbooks/${id}/extract-topics?background=true`);
      if (data.status === "running") {
        setTbDetails(curr => ({ ...curr, [id]: { ...curr[id], topic_extraction: data.topic_extraction } }));
        return;
      }
      setTbDetails((curr) => ({
        ...curr,
        [id]: { ...(curr[id] || {}), topics: data.topics, topic_catalog: data.topic_catalog },
      }));
      toast.success("Topics extracted");
    } catch (err) {
      toast.error(err?.response?.data?.detail || "Failed");
    }
  };

  const setFormatValue = (key, v) => {
    setFormats((curr) => {
      const selectedValue = Math.round(clamp(v, 0, 100));
      const others = curr.filter((format) => format.key !== key);
      if (others.length === 0) {
        return curr.map((format) => ({ ...format, value: 100 }));
      }

      const redistributed = distributeFormatValues(others, 100 - selectedValue);
      const values = new Map(redistributed.map((format) => [format.key, format.value]));
      return curr.map((format) =>
        format.key === key
          ? { ...format, value: selectedValue }
          : { ...format, value: values.get(format.key) }
      );
    });
  };

  const addCustomFormat = () => {
    const label = newFormatLabel.trim();
    if (!label) return;
    const key = label.toLowerCase().replace(/[^a-z0-9]+/g, "_");
    if (formats.some((f) => f.key === key)) {
      toast.error("That format is already in the list");
      return;
    }
    setFormats((curr) => [...curr, { key, label, value: 0, custom: true }]);
    setNewFormatLabel("");
  };

  const removeFormat = (key) => {
    setFormats((curr) => {
      const remaining = curr.filter((format) => format.key !== key);
      return distributeFormatValues(remaining, 100);
    });
  };

  const onGenerate = async (e) => {
    e.preventDefault();
    if (selectedTbIds.length === 0)
      return toast.error("Select at least one textbook");
    if (!title) return toast.error("Title required");
    if (sectionBlueprint.trim() && !blueprint) return toast.error("Analyze your blueprint before generating.");
    if (blueprint && (previewLoading || previewError || !preview?.valid || preview.requestKey !== requestKey)) return toast.error("Resolve the blueprint allocation errors before generating.");
    if (!blueprint && !formatTopicStatus.complete)
      return toast.error("Assign every question format to topics before generating");
    if (!promptOverride && (!Number.isInteger(effectiveQuestionCount) || effectiveQuestionCount < 1 || effectiveQuestionCount > 200))
      return toast.error("Choose between 1 and 200 questions");
    if (!promptOverride && effectiveQuestionCount > Number(totalMarks))
      return toast.error("Allow at least one mark per question");
    if (!promptOverride && info + concept + application !== 100)
      return toast.error("Distribution must sum to 100%");
    if (!promptOverride && formatActive && formatTotal !== 100)
      return toast.error(
        "Format mix must sum to 100% (or set all to 0 to let AI decide)"
      );
    const selectedDiagramCount = countDiagramSelections(blueprint ? blueprintAllocations : formatTopicAllocations);
    if (Number(diagramCount) > MAX_DIAGRAMS_PER_PAPER || selectedDiagramCount > MAX_DIAGRAMS_PER_PAPER)
      return toast.error("Select no more than 5 diagrams");

    // Build format_distribution payload (only when sliders are still active)
    const fmtPayload = {};
    if (!promptOverride) {
      for (const f of formats) {
        const v = Number(f.value || 0);
        if (v > 0) fmtPayload[f.key] = v;
      }
    }

    // Derive subject / class from the first selected book
    const first = tbDetails[selectedTbIds[0]] || {};
    setGenerating(true);
    try {
      const { data } = await api.post("/papers/generate", {
        title,
        subject: first.subject || "",
        class_name: first.class_name || "",
        textbook_ids: selectedTbIds,
        topics: blueprint ? buildTopicPayload(selectedTopics, topicDifficulties, difficulty) : allocatedTopics,
        difficulty,
        duration_minutes: Number(duration),
        total_marks: Number(totalMarks),
        total_questions: promptOverride ? null : effectiveQuestionCount,
        format_counts: null,
        diagram_count: diagramCount === "" ? null : Number(diagramCount),
        distribution: { information: info, concept, application },
        format_distribution: fmtPayload,
        format_topic_allocations: blueprint ? null : formatTopicAllocations,
        custom_instructions: customInstructions.trim() || null,
        section_blueprint: sectionBlueprint.trim() || null,
        blueprint_id: blueprint?.id || null,
        blueprint_version: blueprint?.version || null,
        section_topic_allocations: blueprint ? blueprintAllocations : null,
        branding_id: brandingId || null,
      });
      toast.success("Generation started — opening paper…");
      navigate(`/papers/${data.id}`);
    } catch (err) {
      toast.error(err?.response?.data?.detail || "Generation failed");
    } finally {
      setGenerating(false);
    }
  };

  // Preset buttons either apply the unchanged direct template or open a
  // subject-aware official-variant picker.
  const adoptBlueprint = next => {
    setAllocations(curr => retainCompatibleAllocations(curr, blueprint, next, selectedTbIds));
    setBlueprint(next);
    setTotalMarks(next.total_marks);
    setDuration(next.duration_minutes);
  };
  const selectedSubjects = selectedTbIds.map((id) => (
    tbDetails[id]?.subject || textbooks.find((book) => book.id === id)?.subject || ""
  )).filter(Boolean);
  const subjectMatchesVariant = (next) => {
    const aliases = (next.subject_aliases || []).map((value) => String(value).trim().toLowerCase());
    if (!aliases.length) return true;
    return selectedSubjects.some((subject) => aliases.includes(String(subject).trim().toLowerCase()));
  };
  const applyBlueprint = next => {
    if (!next) return toast.error("Board catalog unavailable. Reload to retry.");
    if (!selectedTbIds.length) return toast.error("Select a matching textbook before applying an official template.");
    if (!next.legacy && !subjectMatchesVariant(next)) {
      return toast.error(`Select a ${next.subject_variant} textbook before applying this template.`);
    }
    adoptBlueprint(next);
    setSectionBlueprint(fillBlueprint(next.template, { subject: selectedSubjects[0] }));
    setBlueprintFamily(null);
  };
  const applyPreset = preset => {
    if (preset.family) return setBlueprintFamily(preset.family);
    applyBlueprint(blueprintCatalog.find(bp => bp.id === preset.id));
  };
  const editBlueprint = text => {
    setSectionBlueprint(text);
    setBlueprint(null);
    setAllocations([]);
  };
  const analyzeBlueprint = async () => {
    setAnalyzing(true);
    try {
      const { data } = await api.post("/paper-blueprints/analyze", { text: sectionBlueprint });
      adoptBlueprint(data);
      toast.success("Blueprint analyzed. Review the section groups and assign topics.");
    } catch (err) { toast.error(err?.response?.data?.detail || "Blueprint analysis failed. Retry after clarifying the section counts and marks."); }
    finally { setAnalyzing(false); }
  };
  const activeTopicExtractionIds = selectedTbIds.filter((id) => isTopicExtractionActive(tbDetails[id]));

  useEffect(() => {
    if (!activeTopicExtractionIds.length) return undefined;
    let cancelled = false;
    const poll = async () => {
      try {
        const results = await Promise.all(activeTopicExtractionIds.map(async (id) => {
          const { data } = await api.get(`/textbooks/${id}`);
          return [id, data];
        }));
        if (cancelled) return;
        setTbDetails(curr => ({ ...curr, ...Object.fromEntries(results) }));
        results.forEach(([, data]) => {
          if (data.topic_extraction?.status === "failed") {
            toast.error(data.topic_extraction.error || "Topic extraction failed. Retry.");
          }
        });
      } catch { if (!cancelled) toast.error("Could not read extraction progress. Refresh the textbook and retry."); }
    };
    poll();
    const timer = setInterval(poll, 2000);
    return () => { cancelled = true; clearInterval(timer); };
  }, [activeTopicExtractionIds.join("|")]); // eslint-disable-line react-hooks/exhaustive-deps
  const requestKey = JSON.stringify({
    title, subject: tbDetails[selectedTbIds[0]]?.subject || "", class_name: tbDetails[selectedTbIds[0]]?.class_name || "",
    textbook_ids: selectedTbIds, topics: blueprint ? buildTopicPayload(selectedTopics, topicDifficulties, difficulty) : allocatedTopics,
    difficulty, duration_minutes: Number(duration), total_marks: Number(totalMarks),
    total_questions: promptOverride ? null : effectiveQuestionCount,
    format_counts: null,
    diagram_count: diagramCount === "" ? null : Number(diagramCount),
    distribution: { information: info, concept, application },
    format_distribution: !promptOverride ? Object.fromEntries(formats.filter(f => f.value > 0).map(f => [f.key, f.value])) : null,
    format_topic_allocations: blueprint ? null : formatTopicAllocations,
    custom_instructions: customInstructions.trim() || null, section_blueprint: sectionBlueprint.trim() || null,
    blueprint_id: blueprint?.id || null, blueprint_version: blueprint?.version || null,
    section_topic_allocations: blueprint ? blueprintAllocations : null,
    branding_id: brandingId || null,
  });
  const { preview, loading: previewLoading, error: previewError } = useConfigurationPreview(requestKey);

  const anyTopics = selectedTbIds.some(
    (id) => (tbDetails[id]?.topics || []).length > 0
  );
  const topicOverview = useMemo(() => {
    if (blueprint) {
      return blueprintAllocations.reduce((rows, allocation) => {
        const topic = tbDetails[allocation.textbook_id]?.topic_catalog?.find(item => item.topic_id === allocation.topic_id);
        const label = topic?.parent_topic ? `${topic.parent_topic} › ${topic.name}` : topic?.name || allocation.topic_id;
        rows[label] = (rows[label] || 0) + allocation.count;
        return rows;
      }, {});
    }
    return formatTopicAllocations.reduce((rows, topic) => {
      const label = topic.parent_topic ? `${topic.parent_topic} › ${topic.name}` : topic.name;
      rows[label] = (rows[label] || 0) + topic.count;
      return rows;
    }, {});
  }, [blueprintAllocations, blueprint, formatTopicAllocations, tbDetails]);
  const sectionTopicOverview = useMemo(() => {
    if (!blueprint) return [];
    return blueprint.sections.map(section => {
      const topics = blueprintAllocations.filter(allocation => allocation.section_id === section.id).reduce((rows, allocation) => {
        const topic = tbDetails[allocation.textbook_id]?.topic_catalog?.find(item => item.topic_id === allocation.topic_id);
        const label = topic?.parent_topic ? `${topic.parent_topic} › ${topic.name}` : topic?.name || allocation.topic_id;
        rows[label] = (rows[label] || 0) + allocation.count;
        return rows;
      }, {});
      return {
        id: section.id,
        title: section.title,
        assigned: Object.values(topics).reduce((total, count) => total + count, 0),
        target: section.groups.reduce((total, group) => total + group.count, 0),
        topics: Object.entries(topics),
      };
    });
  }, [blueprintAllocations, blueprint, tbDetails]);

  return (
    <div className="min-h-screen bg-[#FAFAFA]">
      <Header />
      <main className="max-w-7xl mx-auto p-6 md:p-12" data-testid="new-paper-page">
        <div className="overline text-neutral-500 mb-3">// NEW PAPER</div>
        <h1 className="font-display text-5xl md:text-6xl mb-10">
          Tune the
          <br />
          <span className="text-[#002FA7]">generator.</span>
        </h1>
        <SavedPaperConfigurations currentConfiguration={currentConfiguration} onApply={applySavedConfiguration} />
        <nav className="new-paper-steps" aria-label="Paper creation steps">
          {["Setup", "Questions", "Review"].map((label, index) => <button key={label} type="button" className={step === index ? "is-active" : ""} aria-current={step === index ? "step" : undefined} onClick={() => setStep(index)}>{index + 1}. {label}</button>)}
        </nav>

        <form onSubmit={onGenerate} className="grid grid-cols-1 lg:grid-cols-5 gap-6 md:gap-8">
          {/* Left: Metadata + distribution */}
          <div className="lg:col-span-3 space-y-6">
            {step === 0 && <>
            <ExpandableCard id="metadata" title="Paper details" summary={`${selectedTbIds.length} textbooks · ${totalMarks} marks · ${duration} minutes`} defaultOpen>
              <div className="mb-4">
                <label className="qp-label">Paper title</label>
                <input
                  value={title}
                  onChange={(e) => setTitle(e.target.value)}
                  className="qp-input"
                  placeholder="e.g., Mid-Term Physics Paper"
                  required
                  data-testid="paper-title-input"
                />
              </div>

              <div className="mb-4">
                <label className="qp-label">Textbooks</label>
                <TextbookMultiSelect
                  textbooks={textbooks}
                  selectedIds={selectedTbIds}
                  onChange={setSelectedTextbooks}
                />
              </div>

              <div className="grid grid-cols-1 sm:grid-cols-2 gap-4">
                <div>
                  <label className="qp-label">Duration (min)</label>
                  <input
                    type="number"
                    min={10}
                    max={300}
                    value={duration}
                    onChange={(e) => setDuration(e.target.value)}
                    className="qp-input"
                    data-testid="paper-duration-input"
                  />
                </div>
                <div>
                  <label className="qp-label">Total marks</label>
                  <input
                    type="number"
                    min={10}
                    max={200}
                    value={totalMarks}
                    onChange={(e) => setTotalMarks(e.target.value)}
                    className="qp-input"
                    data-testid="paper-marks-input"
                  />
                </div>
              </div>

              <div className="grid grid-cols-1 sm:grid-cols-2 gap-4 mt-4">
                <div>
                  <label className="qp-label" htmlFor="question-count">Questions</label>
                  <input id="question-count" type="number" min={1} max={200}
                    value={effectiveQuestionCount} disabled={promptOverride}
                    onChange={(e) => setQuestionCount(e.target.value)} className="qp-input"
                    data-testid="paper-question-count-input" />
                  <p className="text-xs text-neutral-500 mt-1">
                    {promptOverride ? "Set by the blueprint" : "Exact number to generate"}
                  </p>
                </div>
                <div>
                  <label className="qp-label" htmlFor="diagram-count">Supplied diagrams</label>
                  <input id="diagram-count" type="number" min={0} max={MAX_DIAGRAMS_PER_PAPER} placeholder="Automatic"
                    value={diagramCount} onChange={(e) => {
                      const raw = e.target.value;
                      if (raw === "") {
                        setDiagramCount("");
                        setDiagramCountError("");
                        return;
                      }
                      const value = Math.max(0, Math.trunc(Number(raw)));
                      if (value > MAX_DIAGRAMS_PER_PAPER) {
                        setDiagramCount(MAX_DIAGRAMS_PER_PAPER);
                        setDiagramCountError("Maximum 5 diagrams allowed.");
                        return;
                      }
                      setDiagramCount(value);
                      setDiagramCountError("");
                    }}
                    aria-invalid={Boolean(diagramCountError)} aria-describedby="diagram-count-help"
                    className="qp-input" data-testid="paper-diagram-count-input" />
                  <p id="diagram-count-help" className={`text-xs mt-1 ${diagramCountError ? "text-red-700 font-semibold" : "text-neutral-500"}`} role={diagramCountError ? "alert" : undefined}>
                    {diagramCountError || "Leave blank for automatic, or 0 for none. (Max 5 diagrams)"}
                  </p>
                </div>
              </div>

              <div className="mt-4 border-t border-neutral-200 pt-4">
                <label className="qp-label">Difficulty</label>
                <div className="grid grid-cols-3 border-2 border-black">
                  {DIFFICULTIES.map((d, i) => (
                    <button
                      key={d}
                      type="button"
                      onClick={() => setDifficulty(d)}
                      data-testid={`difficulty-${d}-button`}
                      className={`py-3 font-bold uppercase tracking-wider text-sm transition-colors ${
                        difficulty === d ? "bg-black text-white" : "bg-white hover:bg-neutral-100"
                      } ${i !== 2 ? "border-r-2 border-black" : ""}`}
                    >
                      {DIFFICULTY_LABELS[d]}
                    </button>
                  ))}
                </div>
              </div>

            </ExpandableCard>

            <ExpandableCard id="distribution" title="Question distribution" summary={promptOverride ? "Controlled by paper blueprint" : `${info}% direct · ${concept}% concept · ${application}% application`} disabled={promptOverride}>
              <div className="flex flex-wrap items-center justify-between gap-2 mb-4">
                <div className="overline">// QUESTION DISTRIBUTION</div>
                {promptOverride ? (
                  <span
                    className="qp-badge qp-badge-yellow"
                    data-testid="distribution-overridden"
                  >
                    OVERRIDDEN BY PROMPT
                  </span>
                ) : (
                  <span
                    className={`qp-badge ${
                      info + concept + application === 100
                        ? "qp-badge-success"
                        : "qp-badge-red"
                    }`}
                    data-testid="distribution-total"
                  >
                    {info + concept + application}%
                  </span>
                )}
              </div>

              <SliderRow
                label="Information / Direct"
                value={info}
                onChange={(v) => setInfo(clamp(v, 0, 100 - concept))}
                testid="dist-info"
                count={typeQuestionCounts.information || 0}
              />
              <SliderRow
                label="Concept"
                value={concept}
                onChange={(v) => setConcept(clamp(v, 0, 100 - info))}
                testid="dist-concept"
                count={typeQuestionCounts.concept || 0}
              />
              <SliderRow
                label="Application"
                value={application}
                readOnly
                color="#E63946"
                testid="dist-app"
                count={typeQuestionCounts.application || 0}
              />
              <p className="text-xs text-neutral-500 font-mono mt-3">
                Application is auto-adjusted so total stays at 100%.
              </p>
            </ExpandableCard>
            </>}

            {step === 1 && <>
            <ExpandableCard id="format-mix" title="Question format mix" summary={promptOverride ? "Controlled by paper blueprint" : formatTopicStage === "mix" ? `${effectiveQuestionCount} questions · ${formats.filter(f => (formatQuestionCounts[f.key] || 0) > 0).length} active formats` : `${assignedFormatTopicCount}/${effectiveQuestionCount} topic slots filled · ${activeFormats.length} question types`} disabled={promptOverride}>
              {formatTopicStage === "mix" ? <>
              <div className="flex flex-wrap items-center justify-between gap-2 mb-2">
                <div className="overline">// QUESTION FORMAT MIX</div>
                {promptOverride ? (
                  <span
                    className="qp-badge qp-badge-yellow"
                    data-testid="format-overridden"
                  >
                    OVERRIDDEN BY PROMPT
                  </span>
                ) : (
                  <span
                    className={`qp-badge ${
                      formatTotal === 100
                        ? "qp-badge-success"
                        : "qp-badge-red"
                    }`}
                    data-testid="format-total"
                  >
                    {formatTotal}%
                  </span>
                )}
              </div>
              <p className="text-xs text-neutral-500 font-mono mb-4">
                MCQ, Short / Long Answer, Fill-in-the-blanks, True / False, or
                add your own. Set the percentage mix; it is converted to the
                whole-question counts shown below.
              </p>
              {formats.map((f) => (
                <div
                  key={f.key}
                  className="mb-3 last:mb-0"
                  data-testid={`format-${f.key}`}
                >
                  <div className="flex flex-wrap items-center justify-between gap-2 mb-1">
                    <div className="font-bold text-sm uppercase tracking-wider flex items-center gap-2">
                      {f.label}
                      {f.custom && (
                        <button
                          type="button"
                          onClick={() => removeFormat(f.key)}
                          className="text-neutral-400 hover:text-[#E63946]"
                          aria-label="Remove custom format"
                          data-testid={`format-remove-${f.key}`}
                        >
                          <XIcon size={12} weight="bold" />
                        </button>
                      )}
                    </div>
                    <div className="font-mono text-sm font-bold">
                      {f.value}% · {formatQuestionCounts[f.key] || 0} questions
                    </div>
                  </div>
                  <input
                    type="range"
                    min={0}
                    max={100}
                    value={f.value}
                    onChange={(e) => setFormatValue(f.key, Number(e.target.value))}
                    className="w-full mt-1"
                    data-testid={`format-${f.key}-slider`}
                  />
                </div>
              ))}

              <div className="mt-4 flex items-center gap-2 border-t-2 border-black pt-3">
                <input
                  type="text"
                  value={newFormatLabel}
                  onChange={(e) => setNewFormatLabel(e.target.value)}
                  onKeyDown={(e) => {
                    if (e.key === "Enter") {
                      e.preventDefault();
                      addCustomFormat();
                    }
                  }}
                  placeholder="Custom format (e.g., Case Study)"
                  className="qp-input flex-1"
                  data-testid="custom-format-input"
                />
                <button
                  type="button"
                  onClick={addCustomFormat}
                  className="qp-btn qp-btn-secondary text-xs"
                  data-testid="custom-format-add"
                >
                  <Plus size={14} weight="bold" /> Add
                </button>
              </div>
              {!promptOverride && <button type="button" className="qp-btn qp-btn-primary w-full mt-4" data-testid="format-topics-next"
                disabled={!activeFormats.length || formatTotal !== 100}
                onClick={() => setFormatTopicStage("allocate")}>Next: assign topics<ArrowRight size={16} weight="bold" /></button>}
              </> : <FormatTopicAllocator formats={activeFormats} allocations={formatTopicAllocations}
                onChange={(next) => { setFormatTopicAllocations(next); setFormatTopicStage("allocate"); }} bookIds={selectedTbIds} details={tbDetails}
                difficulty={difficulty} onExtract={extractTopicsFor} onBack={() => setFormatTopicStage("mix")}
                onConfirm={() => { setFormatTopicStage("confirmed"); toast.success("Question formats and topics confirmed."); }} />}
            </ExpandableCard>

            <ExpandableCard id="blueprint" title="Paper blueprint" summary={sectionBlueprint.trim() ? "Custom blueprint applied" : "Optional · Choose a preset or define sections"}>
              <p className="text-xs text-neutral-500 font-mono mb-3">
                Paste your board&rsquo;s exact section pattern. When set, this
                <b> overrides </b> the Question Distribution and Question Format
                Mix above &mdash; the AI follows the blueprint verbatim. Useful
                for ICSE / CBSE / ISC / IGCSE / state-board templates. The
                presets fill in the subject of the first selected textbook.
              </p>
              <div className="flex flex-wrap gap-2 mb-3">
                {BLUEPRINT_PRESETS.map((preset) => (
                  <button
                    key={preset.id}
                    type="button"
                    onClick={() => applyPreset(preset)}
                    className="qp-btn qp-btn-secondary text-xs"
                    data-testid={preset.testId}
                  >
                    {preset.label}
                  </button>
                ))}
                {sectionBlueprint && (
                  <button
                    type="button"
                    onClick={() => editBlueprint("")}
                    className="qp-btn qp-btn-secondary text-xs"
                    data-testid="blueprint-clear"
                  >
                    <XIcon size={12} weight="bold" /> Clear
                  </button>
                )}
              </div>
              {blueprintFamily && (() => {
                const current = blueprintCatalog.filter((item) => item.family === blueprintFamily && !item.legacy);
                const legacy = blueprintCatalog.filter((item) => item.family === blueprintFamily && item.legacy);
                const familyLabel = BLUEPRINT_PRESETS.find((item) => item.family === blueprintFamily)?.label || "Board template";
                return <div className="border border-neutral-300 bg-white p-3 mb-3" data-testid="blueprint-variant-picker">
                  <div className="flex items-center justify-between gap-3 mb-2"><div><strong className="text-sm">Choose {familyLabel} subject</strong><p className="text-xs text-neutral-500">Official current STEM patterns</p></div><button type="button" className="text-xs underline" onClick={() => setBlueprintFamily(null)}>Close</button></div>
                  <div className="grid gap-2 sm:grid-cols-2">
                    {current.map((item) => <button key={item.id} type="button" onClick={() => applyBlueprint(item)} className="border border-neutral-300 p-2 text-left hover:border-blue-700" data-testid={`blueprint-variant-${item.id}`}><strong className="block text-xs">{item.subject_variant}</strong><span className="block text-xs text-neutral-600">{item.total_marks} marks · {item.duration_minutes / 60}h · {item.academic_session}</span><span className="block text-[10px] text-blue-700 mt-1">Official current format</span></button>)}
                  </div>
                  {legacy.length > 0 && <details className="mt-3 text-xs"><summary className="cursor-pointer font-semibold">Legacy templates</summary><div className="grid gap-2 sm:grid-cols-2 mt-2">{legacy.map((item) => <button key={item.id} type="button" onClick={() => applyBlueprint(item)} className="border border-amber-300 bg-amber-50 p-2 text-left"><strong>{item.subject_variant}</strong><span className="block text-neutral-600">Previous generic format · validation status shown before generation</span></button>)}</div></details>}
                </div>;
              })()}
              <textarea
                value={sectionBlueprint}
                onChange={(e) => editBlueprint(e.target.value)}
                rows={8}
                className="qp-input font-mono text-xs"
                placeholder={`Example:
Section A (40 marks, compulsory)
- Q1: 15 MCQs x 1 mark each (multiple-choice with 4 options)
- Q2: 6 fill-in-the-blanks x 1 mark each
- Q3: 4 short-answer parts (a)-(d), 19 marks total

Section B (40 marks) - Attempt any FOUR of the following SIX questions
- Q4-Q9: 10 marks each, sub-divided into parts (a) 3m + (b) 3m + (c) 4m`}
                spellCheck={false}
                data-testid="blueprint-input"
              />
            </ExpandableCard>

            {sectionBlueprint.trim() && !blueprint && <button type="button" onClick={analyzeBlueprint} disabled={analyzing} className="qp-btn qp-btn-secondary" data-testid="analyze-blueprint">{analyzing ? "Analyzing blueprint…" : "Analyze blueprint"}</button>}
            <SectionBlueprintEditor blueprint={blueprint} bookIds={selectedTbIds} details={tbDetails} allocations={blueprintAllocations} onChange={setAllocations} difficulty={difficulty} onExtract={extractTopicsFor} />
            <ExpandableCard id="custom-instructions" title="Additional instructions" summary={customInstructions.trim() ? customInstructions.trim() : "Optional · Refine how questions are generated"}>
              <p className="text-xs text-neutral-500 font-mono mb-3">
                Optional. Your form settings already define the paper. Add only
                refinements or exceptions here; an explicit instruction overrides
                the setting it conflicts with. Example:&nbsp;
                <span className="text-neutral-700">
                  &ldquo;Use whole numbers. Put Mechanics in Section A and
                  Electricity in Section B.&rdquo;
                </span>
              </p>
              <textarea
                value={customInstructions}
                onChange={(e) => setCustomInstructions(e.target.value)}
                rows={4}
                className="qp-input font-mono text-sm"
                placeholder={`Layered on top of the settings above. Examples:
10 MCQs and 2 long questions
Make Section A hard, Section B medium
Make Q23 as hard as possible`}
                spellCheck={false}
                data-testid="custom-instructions-input"
              />
            </ExpandableCard>
            </>}

            {step === 2 && <section className="qp-card" data-testid="paper-review">
              <h2 className="font-bold uppercase tracking-wide text-sm">Paper overview</h2>
              <p className="text-xs text-neutral-500 mt-2 mb-4">Review the overall composition before generating the paper.</p>
              <dl className="blueprint-summary-facts">
                <div><dt>Title</dt><dd>{title || "Untitled paper"}</dd></div>
                <div><dt>Textbooks</dt><dd>{selectedTbIds.length}</dd></div>
                <div><dt>Marks / duration</dt><dd>{totalMarks} marks · {duration} min</dd></div>
                <div><dt>Question target</dt><dd>{promptOverride ? "Defined by blueprint" : effectiveQuestionCount}</dd></div>
                <div><dt>Difficulty</dt><dd>{DIFFICULTY_LABELS[difficulty]}</dd></div>
                <div><dt>Blueprint</dt><dd>{blueprint?.label || "No blueprint"}</dd></div>
                <div>
                  <dt>Branding</dt>
                  <dd>
                    <select value={brandingId} onChange={(event) => setBrandingId(event.target.value)} className="qp-input py-1 text-sm" data-testid="paper-branding-select">
                      <option value="">No branding</option>
                      {brandingPresets.map((preset) => <option key={preset.id} value={preset.id}>{preset.name}</option>)}
                    </select>
                  </dd>
                </div>
              </dl>
              <h3 className="text-xs font-bold uppercase mt-5 mb-2">Topic-wise composition</h3>
              {Object.keys(topicOverview).length ? <div className="review-topic-list">{Object.entries(topicOverview).map(([topic, count]) => <div key={topic}><span>{topic}</span><strong>{count} {blueprint ? "questions" : "selected"}</strong></div>)}</div> : <p className="text-sm text-neutral-500">Choose topics in the persistent panel to see their composition here.</p>}
              {blueprint && <><h3 className="text-xs font-bold uppercase mt-5 mb-2">Section-wise topic composition</h3><div className="review-section-list">{sectionTopicOverview.map(section => <section key={section.id} className="review-section-composition"><div className="review-section-heading"><strong>{section.title}</strong><span>{section.assigned}/{section.target} questions</span></div>{section.topics.length ? <div className="review-topic-list">{section.topics.map(([topic, count]) => <div key={topic}><span>{topic}</span><strong>{count} questions</strong></div>)}</div> : <p className="text-xs text-neutral-500 py-2">No topics assigned to this section.</p>}</section>)}</div></>}
            </section>}
          </div>

          {/* Right: Topics + submit */}
          <div className="lg:col-span-2 space-y-6">
            <ConfigurationSummary preview={preview?.requestKey === requestKey ? preview : null} loading={previewLoading} error={previewError} />
            {false && !blueprint && <ExpandableCard id="topics" title="Topic selection (no blueprint)" summary={`${selectedTopics.length} selected · Default: ${DIFFICULTY_LABELS[difficulty]}`} defaultOpen>

              {selectedTbIds.length === 0 && (
                <div className="text-sm text-neutral-500">
                  Select one or more textbooks to load topics.
                </div>
              )}

              {selectedTbIds.length > 0 && anyTopics && (
                <p className="text-xs text-neutral-500 font-mono mb-3">
                  Pick topics or subtopics from any book. Subtopics use their topic’s
                  difficulty unless you choose a separate level.
                </p>
              )}

              <div
                className="chapter-selection-scroll space-y-5"
                aria-label="Chapters and subtopics"
                data-testid="topic-selection-scroll"
              >
                {selectedTbIds.map((bookId) => {
                  const detail = tbDetails[bookId];
                  const tb = textbooks.find((t) => t.id === bookId);
                  const topics = detail?.topics || [];
                  const bookTopicNames = topicsForBook(bookId, topics).map((topic) => topic.key);
                  const selectedNameSet = new Set(
                    selectedTopics.map((topic) => topic.key)
                  );
                  const selectedBookTopicCount = bookTopicNames.filter((name) =>
                    selectedNameSet.has(name)
                  ).length;
                  const allBookTopicsSelected =
                    bookTopicNames.length > 0 &&
                    selectedBookTopicCount === bookTopicNames.length;
                  const someBookTopicsSelected =
                    selectedBookTopicCount > 0 && !allBookTopicsSelected;
                  return (
                    <div key={bookId} data-testid={`book-topics-${bookId}`}>
                      <div className="flex items-center justify-between mb-2">
                        <div className="overline text-neutral-500 truncate">
                          {(tb?.original_filename || bookId).slice(0, 40)}
                        </div>
                        <button
                          type="button"
                          onClick={() => extractTopicsFor(bookId)}
                          disabled={isTopicExtractionActive(detail)}
                          className="qp-btn-ghost text-xs uppercase tracking-wider"
                          data-testid={`extract-topics-${bookId}`}
                        >
                          {detail?.topic_extraction?.status === "queued"
                            ? "Queued"
                            : detail?.topic_extraction?.status === "running"
                            ? "..."
                            : topics.length
                            ? "Re-extract"
                            : "Extract"}
                        </button>
                      </div>

                      {!detail && (
                        <div className="text-xs text-neutral-400">Loading...</div>
                      )}

                      {detail && topics.length === 0 && (
                        <button
                          type="button"
                          onClick={() => extractTopicsFor(bookId)}
                          disabled={isTopicExtractionActive(detail)}
                          className="qp-btn qp-btn-primary w-full text-xs"
                          data-testid={`extract-now-${bookId}`}
                        >
                          <Sparkle size={12} weight="bold" />
                          {detail?.topic_extraction?.status === "queued"
                            ? "Queued"
                            : detail?.topic_extraction?.status === "running"
                            ? "Extracting..."
                            : "Extract topics"}
                        </button>
                      )}

                      {topics.length > 0 && (
                        <div className="space-y-2">
                          <button
                            type="button"
                            onClick={() => toggleAllTopicsForBook(bookId, topics)}
                            className="flex items-center gap-2 w-full border-2 border-black bg-white p-2 text-left hover:bg-neutral-50"
                            aria-pressed={allBookTopicsSelected}
                            data-testid={`topics-select-all-${bookId}`}
                          >
                            {allBookTopicsSelected ? (
                              <CheckSquare
                                size={18}
                                weight="fill"
                                color="#002FA7"
                              />
                            ) : someBookTopicsSelected ? (
                              <MinusSquare
                                size={18}
                                weight="fill"
                                color="#002FA7"
                              />
                            ) : (
                              <Square size={18} weight="bold" />
                            )}
                            <span className="text-sm font-bold">Select all</span>
                            <span className="ml-auto font-mono text-xs text-neutral-500">
                              {selectedBookTopicCount}/{bookTopicNames.length}
                            </span>
                          </button>
                          {topics.map((t, i) => {
                            const expansionKey = topicKey(bookId, t.name);
                            const expanded = !!expandedTopics[expansionKey];
                            const selectedSubtopics = (t.subtopics || []).filter((name) =>
                              selectedTopics.some((topic) => topic.key === topicKey(bookId, name, t.name))
                            ).length;
                            const items = [
                              { name: t.name, isTopic: true },
                              ...((t.subtopics || []).map((s) => ({
                                name: s,
                                isTopic: false,
                              }))),
                            ];
                            return (
                              <div
                                key={i}
                                className="border-2 border-black bg-white"
                                data-testid={`topic-block-${bookId}-${i}`}
                              >
                                {items.map((it, j) => {
                                  const key = topicKey(bookId, it.name, it.isTopic ? "" : t.name);
                                  const sel = selectedTopics.find(
                                    (s) => s.key === key
                                  );
                                  const checked = !!sel;
                                  const inheritedDifficulty = it.isTopic ? difficulty :
                                    topicDifficulties[topicKey(bookId, t.name)] || difficulty;
                                  return (
                                    <div
                                      key={j}
                                      hidden={!it.isTopic && !expanded}
                                      style={!it.isTopic && !expanded ? { display: "none" } : undefined}
                                      className={`flex flex-wrap items-center gap-2 p-2 ${
                                        j !== items.length - 1
                                          ? "border-b border-neutral-200"
                                          : ""
                                      } ${it.isTopic ? "bg-neutral-50" : "pl-8"}`}
                                      data-testid={`topic-row-${bookId}-${i}-${j}`}
                                    >
                                      <button
                                        type="button"
                                        onClick={() => toggleTopic(bookId, it.name, it.isTopic ? "" : t.name)}
                                        aria-pressed={checked}
                                        className="flex items-center gap-2 flex-1 text-left"
                                        data-testid={`topic-toggle-${bookId}-${i}-${j}`}
                                      >
                                        {checked ? (
                                          <CheckSquare
                                            size={18}
                                            weight="fill"
                                            color="#002FA7"
                                          />
                                        ) : (
                                          <Square size={18} weight="bold" />
                                        )}
                                        <span
                                          className={`text-sm ${
                                            it.isTopic ? "font-bold" : ""
                                          }`}
                                        >
                                          {it.name}
                                        </span>
                                      </button>
                                      {(checked || it.isTopic) && (
                                        <select
                                          className="qp-input text-xs py-1 w-auto max-w-full"
                                          aria-label={`Difficulty for ${it.name}${it.isTopic ? "" : ` in ${t.name}`}`}
                                          value={topicDifficulties[key] || ""}
                                          onChange={(e) => setTopicDifficulties((curr) => ({
                                            ...curr, [key]: e.target.value,
                                          }))}
                                          data-testid={`topic-difficulty-${bookId}-${i}-${j}`}
                                        >
                                          <option value="">
                                            {it.isTopic ? "Topic" : "SubTopic"}: {DIFFICULTY_LABELS[inheritedDifficulty]}
                                          </option>
                                          {DIFFICULTIES.map((level) => (
                                            <option key={level} value={level}>{DIFFICULTY_LABELS[level]}</option>
                                          ))}
                                        </select>
                                      )}
                                    </div>
                                  );
                                })}
                                {items.length > 1 && (
                                  <button
                                    type="button"
                                    aria-expanded={expanded}
                                    aria-label={`${expanded ? "Collapse" : "Expand"} subtopics for ${t.name}`}
                                    onClick={() => setExpandedTopics((curr) => ({ ...curr, [expansionKey]: !curr[expansionKey] }))}
                                    className="flex w-full items-center justify-between gap-2 border-t border-neutral-200 px-3 py-2 text-xs font-bold text-[#002FA7] hover:bg-blue-50 focus-visible:outline focus-visible:outline-2 focus-visible:outline-[#002FA7]"
                                    data-testid={`topic-expand-${bookId}-${i}`}
                                  >
                                    <span>{expanded ? "Show less" : "Expand more"} · {items.length - 1} subtopics ({selectedSubtopics} selected)</span>
                                    <CaretDown size={16} className={`shrink-0 transition-transform ${expanded ? "rotate-180" : ""}`} aria-hidden="true" />
                                  </button>
                                )}
                              </div>
                            );
                          })}
                        </div>
                      )}
                    </div>
                  );
                })}
              </div>
            </ExpandableCard>}

            {step < 2 ? <button type="button" disabled={step === 1 && !blueprint && (!formatTopicStatus.complete || formatTopicStage !== "confirmed")} onClick={() => setStep(current => current + 1)} className="qp-btn qp-btn-primary w-full text-base py-4" data-testid="save-next-button">Save &amp; next<ArrowRight size={16} weight="bold" /></button> : <button type="submit" disabled={generating} className="qp-btn qp-btn-primary w-full text-base py-4" data-testid="generate-paper-button"><Sparkle size={18} weight="bold" />{generating ? "Generating..." : "Save and generate"}<ArrowRight size={16} weight="bold" /></button>}
          </div>
        </form>
      </main>
    </div>
  );
}

const clamp = (v, lo, hi) => Math.max(lo, Math.min(hi, Number(v)));

function ExpandableCard({ id, title, summary, defaultOpen = false, disabled = false, children }) {
  const [open, setOpen] = useState(defaultOpen);
  return (
    <section className="qp-card" data-testid={`${id}-card`}>
      <button
        type="button"
        className="flex w-full items-start justify-between gap-4 text-left focus-visible:outline focus-visible:outline-2 focus-visible:outline-offset-4 focus-visible:outline-[#002FA7]"
        onClick={() => setOpen((value) => !value)}
        aria-expanded={open}
        aria-controls={`${id}-settings`}
        data-testid={`${id}-expand`}
      >
        <span className="min-w-0">
          <span className="block font-bold text-sm uppercase tracking-wider">{title}</span>
          <span className="mt-2 block text-xs text-neutral-500 break-words line-clamp-2">{summary}</span>
        </span>
        <span className="flex shrink-0 items-center gap-1 text-xs font-bold text-[#002FA7]">
          <span>{open ? "Show less" : "Expand more"}</span>
          <CaretDown size={16} className={`transition-transform ${open ? "rotate-180" : ""}`} aria-hidden="true" />
        </span>
      </button>
      <div id={`${id}-settings`} hidden={!open} className="mt-5 border-t border-neutral-200 pt-5"
        onInvalidCapture={() => flushSync(() => setOpen(true))}>
        <fieldset disabled={disabled} className={`min-w-0 ${disabled ? "opacity-50" : ""}`}>
          <legend className="sr-only">{title}</legend>
          {children}
        </fieldset>
      </div>
    </section>
  );
}

// Scale a group of format values to an exact integer total. Existing ratios
// are preserved where possible; if every value is zero, the space is shared
// evenly. Largest remainders receive the leftover points so rounding can never
// make the displayed total drift away from 100%.
const distributeFormatValues = (formats, targetTotal) => {
  if (formats.length === 0) return [];

  const currentTotal = formats.reduce(
    (sum, format) => sum + Number(format.value || 0),
    0
  );
  const rawValues = formats.map((format) =>
    currentTotal > 0
      ? (Number(format.value || 0) / currentTotal) * targetTotal
      : targetTotal / formats.length
  );
  const values = rawValues.map(Math.floor);
  let remainder = targetTotal - values.reduce((sum, value) => sum + value, 0);
  const remainderOrder = rawValues
    .map((value, index) => ({ index, fraction: value - Math.floor(value) }))
    .sort((a, b) => b.fraction - a.fraction || a.index - b.index);

  for (let i = 0; i < remainder; i += 1) {
    values[remainderOrder[i % remainderOrder.length].index] += 1;
  }

  return formats.map((format, index) => ({ ...format, value: values[index] }));
};

const SliderRow = ({ label, value, count, onChange, readOnly, color, testid }) => (
  <div className="mb-4 last:mb-0" data-testid={testid}>
    <div className="flex items-center justify-between mb-2">
      <div className="font-bold text-sm uppercase tracking-wider">{label}</div>
      <div className="font-mono text-sm font-bold">{value}% · {count} questions</div>
    </div>
    {readOnly ? (
      <div className="relative h-2 bg-neutral-200" aria-label={`${label} progress`}>
        <div
          className="absolute top-0 left-0 h-full"
          style={{ width: `${value}%`, background: color }}
        />
      </div>
    ) : (
      <input
        type="range"
        min={0}
        max={100}
        value={value}
        onChange={(e) => onChange(Number(e.target.value))}
        className="w-full mt-1"
        data-testid={`${testid}-slider`}
      />
    )}
  </div>
);
