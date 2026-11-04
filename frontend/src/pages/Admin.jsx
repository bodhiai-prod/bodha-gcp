import React, { useEffect, useState } from "react";
import { Link } from "react-router-dom";
import { api } from "@/lib/api";
import Header from "@/components/Header";
import AdminUsage from "@/components/AdminUsage";
import { toast } from "sonner";
import { useConfirmation } from "@/context/ConfirmationContext";
import {
  Users,
  Books,
  Stack,
  ShareNetwork,
  Sparkle,
  Trash,
  UploadSimple,
  ArrowClockwise,
  Eye,
  MagnifyingGlass,
  WarningCircle,
  Clock,
  GraduationCap,
  ChartBar,
  X,
} from "@phosphor-icons/react";

const TABS = [
  { key: "usage", label: "Usage & reliability" },
  { key: "library", label: "Shared library" },
  { key: "textbooks", label: "All textbooks" },
  { key: "papers", label: "All papers" },
  { key: "users", label: "Users" },
  { key: "activity", label: "Activity" },
  { key: "upload", label: "Bulk upload" },
];

export default function Admin() {
  const confirm = useConfirmation();
  const [tab, setTab] = useState("library");
  const [overview, setOverview] = useState(null);
  const [textbooks, setTextbooks] = useState([]);
  const [papers, setPapers] = useState([]);
  const [users, setUsers] = useState([]);
  const [userTotal, setUserTotal] = useState(0);
  const [userFilters, setUserFilters] = useState({ q: "", role: "", board: "", profile: "", activity: "" });
  const [selectedUser, setSelectedUser] = useState(null);
  const [activity, setActivity] = useState([]);
  const [loading, setLoading] = useState(false);
  const [reindexingId, setReindexingId] = useState(null);

  const loadOverview = async () => {
    try {
      const { data } = await api.get("/admin/overview");
      setOverview(data);
    } catch (err) {
      toast.error(err?.response?.data?.detail || "Failed to load overview");
    }
  };

  const loadTextbooks = async () => {
    setLoading(true);
    try {
      const { data } = await api.get("/admin/textbooks");
      setTextbooks(data);
    } finally {
      setLoading(false);
    }
  };

  const loadPapers = async () => {
    setLoading(true);
    try {
      const { data } = await api.get("/admin/papers");
      setPapers(data);
    } finally {
      setLoading(false);
    }
  };

  const loadUsers = async (filters = userFilters) => {
    setLoading(true);
    try {
      const params = Object.fromEntries(Object.entries(filters).filter(([, value]) => value));
      const { data } = await api.get("/admin/users", { params: { ...params, page_size: 100 } });
      setUsers(data.items);
      setUserTotal(data.total);
    } finally {
      setLoading(false);
    }
  };

  const loadActivity = async () => {
    setLoading(true);
    try {
      const { data } = await api.get("/admin/activity", { params: { page_size: 50 } });
      setActivity(data.items);
    } finally {
      setLoading(false);
    }
  };

  const openUser = async (id) => {
    try {
      const { data } = await api.get(`/admin/users/${id}`);
      setSelectedUser(data);
    } catch (err) {
      toast.error(err?.response?.data?.detail || "Could not load user details");
    }
  };

  useEffect(() => {
    loadOverview();
    loadTextbooks();
  }, []);

  useEffect(() => {
    if (tab === "papers" && papers.length === 0) loadPapers();
    if (tab === "users" && users.length === 0) loadUsers();
    if (tab === "activity" && activity.length === 0) loadActivity();
  }, [tab]); // eslint-disable-line

  const toggleShare = async (tb) => {
    try {
      await api.patch(
        `/admin/textbooks/${tb.id}/share?is_shared=${!tb.is_shared}`
      );
      toast.success(
        !tb.is_shared
          ? `"${tb.original_filename}" added to shared library`
          : `"${tb.original_filename}" removed from shared library`
      );
      await Promise.all([loadTextbooks(), loadOverview()]);
    } catch (err) {
      toast.error(err?.response?.data?.detail || "Failed");
    }
  };

  const reindex = async (tb) => {
    setReindexingId(tb.id);
    try {
      await api.post(`/textbooks/${tb.id}/reindex`);
      toast.success("Re-indexed");
      await loadTextbooks();
    } catch (err) {
      toast.error(err?.response?.data?.detail || "Re-index failed");
    } finally {
      setReindexingId(null);
    }
  };

  const removeTextbook = async (tb) => {
    if (!(await confirm({
      title: "Delete textbook?",
      description: `Delete "${tb.original_filename}" (owned by ${tb.owner_email})?`,
      confirmLabel: "Delete textbook",
      tone: "danger",
    })))
      return;
    try {
      await api.delete(`/textbooks/${tb.id}`);
      toast.success("Deleted");
      await Promise.all([loadTextbooks(), loadOverview()]);
    } catch (err) {
      toast.error(err?.response?.data?.detail || "Failed");
    }
  };

  return (
    <div className="min-h-screen bg-[#FAFAFA]">
      <Header />
      <main className="max-w-7xl mx-auto p-6 md:p-12" data-testid="admin-page">
        <div className="overline text-neutral-500 mb-3">// Admin console</div>
        <h1 className="font-display text-5xl md:text-6xl mb-10">
          School
          <br />
          <span className="text-[#002FA7]">control room.</span>
        </h1>

        {/* Stats */}
        {overview && (
          <div
            className="grid grid-cols-1 min-[420px]:grid-cols-2 md:grid-cols-5 gap-4 mb-10"
            data-testid="admin-stats"
          >
            <StatCard
              icon={<Users size={20} weight="bold" />}
              label="All users"
              value={overview.users_total}
              testid="stat-teachers"
            />
            <StatCard
              icon={<Books size={20} weight="bold" />}
              label="Textbooks"
              value={overview.textbooks_total}
              testid="stat-textbooks"
            />
            <StatCard
              icon={<ShareNetwork size={20} weight="bold" />}
              label="Shared library"
              value={overview.textbooks_shared}
              accent
              testid="stat-shared"
            />
            <StatCard
              icon={<Stack size={20} weight="bold" />}
              label="Papers"
              value={overview.papers_total}
              testid="stat-papers"
            />
            <StatCard
              icon={<Sparkle size={20} weight="bold" />}
              label="In progress"
              value={overview.papers_pending}
              testid="stat-pending"
              warn={overview.papers_pending > 0}
            />
          </div>
        )}

        {overview && <OperationalOverview overview={overview} />}

        {/* Tabs */}
        <div
          className="flex flex-wrap gap-1 border-b-2 border-black mb-6"
          data-testid="admin-tabs"
        >
          {TABS.map((t) => (
            <button
              key={t.key}
              onClick={() => setTab(t.key)}
              className={`px-4 py-2 font-medium text-xs transition-colors ${
                tab === t.key
                  ? "bg-black text-white"
                  : "bg-white hover:bg-neutral-100"
              }`}
              data-testid={`admin-tab-${t.key}`}
            >
              {t.label}
            </button>
          ))}
        </div>

        {tab === "library" && (
          <SharedLibraryTab
            textbooks={textbooks.filter((t) => t.is_shared)}
            allTextbooks={textbooks}
            loading={loading}
            onToggleShare={toggleShare}
          />
        )}
        {tab === "textbooks" && (
          <AllTextbooksTab
            textbooks={textbooks}
            loading={loading}
            onToggleShare={toggleShare}
            onReindex={reindex}
            onDelete={removeTextbook}
            reindexingId={reindexingId}
          />
        )}
        {tab === "usage" && <AdminUsage />}
        {tab === "papers" && <AllPapersTab papers={papers} loading={loading} />}
        {tab === "users" && <UsersTab users={users} total={userTotal} filters={userFilters} onFiltersChange={(next) => { setUserFilters(next); loadUsers(next); }} onOpen={openUser} loading={loading} />}
        {tab === "activity" && <ActivityTab activity={activity} loading={loading} />}
        {tab === "upload" && (
          <BulkUploadTab onDone={() => Promise.all([loadTextbooks(), loadOverview()])} />
        )}
      </main>
      {selectedUser && <UserDetailDrawer detail={selectedUser} onClose={() => setSelectedUser(null)} />}
    </div>
  );
}

const StatCard = ({ icon, label, value, accent, warn, testid }) => (
  <div
    className={`border-2 border-black bg-white p-4 ${
      accent ? "bg-[#002FA7] text-white border-[#002FA7]" : ""
    } ${warn ? "bg-[#FFC300] text-black border-black" : ""}`}
    data-testid={testid}
  >
    <div className="flex items-center gap-2 opacity-80">
      {icon}
      <div className="overline">{label}</div>
    </div>
    <div className="font-display text-4xl mt-2">{value}</div>
  </div>
);

const SharedLibraryTab = ({ textbooks, allTextbooks, loading, onToggleShare }) => {
  if (loading) return <Loading />;
  return (
    <div data-testid="library-tab">
      <p className="text-sm text-neutral-600 font-mono mb-4">
        Books in the shared library appear in every teacher&rsquo;s textbook
        list (read-only). Toggle a book here to add or remove it from the
        library.
      </p>
      {textbooks.length === 0 ? (
        <Empty
          message="No books in the shared library yet."
          hint="Open the All Textbooks tab and click Share to add one."
        />
      ) : (
        <div className="space-y-2" data-testid="library-list">
          {textbooks.map((tb) => (
            <TextbookRow
              key={tb.id}
              tb={tb}
              onToggleShare={onToggleShare}
              showShareToggle
            />
          ))}
        </div>
      )}
      {allTextbooks.length > textbooks.length && (
        <div className="mt-6 text-xs text-neutral-500 font-mono">
          {allTextbooks.length - textbooks.length} more textbook(s) available in{" "}
          <b>All textbooks</b> tab.
        </div>
      )}
    </div>
  );
};

const AllTextbooksTab = ({
  textbooks,
  loading,
  onToggleShare,
  onReindex,
  onDelete,
  reindexingId,
}) => {
  const [filter, setFilter] = useState("");
  if (loading) return <Loading />;
  const filtered = textbooks.filter(
    (t) =>
      !filter ||
      t.original_filename.toLowerCase().includes(filter.toLowerCase()) ||
      t.subject.toLowerCase().includes(filter.toLowerCase()) ||
      t.owner_email.toLowerCase().includes(filter.toLowerCase())
  );
  return (
    <div data-testid="textbooks-tab">
      <div className="flex items-center justify-between mb-4 gap-3 flex-wrap">
        <input
          type="text"
          value={filter}
          onChange={(e) => setFilter(e.target.value)}
          placeholder="Filter by filename / subject / owner email"
          className="qp-input flex-1 max-w-md"
          data-testid="textbook-filter"
        />
        <div className="text-xs font-mono text-neutral-500">
          {filtered.length} of {textbooks.length}
        </div>
      </div>
      {filtered.length === 0 ? (
        <Empty message="No textbooks match." />
      ) : (
        <div className="space-y-2" data-testid="all-textbooks-list">
          {filtered.map((tb) => (
            <TextbookRow
              key={tb.id}
              tb={tb}
              onToggleShare={onToggleShare}
              onReindex={onReindex}
              onDelete={onDelete}
              showShareToggle
              showReindex
              showDelete
              reindexing={reindexingId === tb.id}
            />
          ))}
        </div>
      )}
    </div>
  );
};

const TextbookRow = ({
  tb,
  onToggleShare,
  onReindex,
  onDelete,
  showShareToggle,
  showReindex,
  showDelete,
  reindexing,
}) => (
  <div
    className="border-2 border-black bg-white p-3 flex items-center gap-3 flex-wrap"
    data-testid={`admin-tb-${tb.id}`}
  >
    <div className="flex-1 min-w-0">
      <div className="font-medium truncate">{tb.original_filename}</div>
      <div className="text-xs font-mono text-neutral-500 mt-0.5">
        {tb.subject} · Class {tb.class_name} · {tb.chunk_count} chunks ·{" "}
        <span className="text-neutral-700">{tb.owner_email}</span>
      </div>
    </div>
    <div className="flex flex-wrap gap-1">
      {tb.is_shared && (
        <span className="qp-badge qp-badge-yellow">shared</span>
      )}
      <span
        className={`qp-badge ${
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
        <span className="qp-badge">{tb.topic_count} topics</span>
      )}
    </div>
    <div className="flex flex-wrap gap-2">
      {showShareToggle && (
        <button
          onClick={() => onToggleShare(tb)}
          className={`qp-btn text-xs ${
            tb.is_shared ? "qp-btn-secondary" : "qp-btn-primary"
          }`}
          data-testid={`admin-toggle-share-${tb.id}`}
        >
          <ShareNetwork size={14} weight="bold" />
          {tb.is_shared ? "Unshare" : "Share"}
        </button>
      )}
      {showReindex && (
        <button
          onClick={() => onReindex(tb)}
          disabled={reindexing}
          className="qp-btn qp-btn-secondary text-xs"
          data-testid={`admin-reindex-${tb.id}`}
        >
          <ArrowClockwise size={14} weight="bold" />
          {reindexing ? "..." : "Re-index"}
        </button>
      )}
      {showDelete && (
        <button
          onClick={() => onDelete(tb)}
          className="qp-btn qp-btn-secondary text-xs"
          aria-label="Delete"
          data-testid={`admin-delete-tb-${tb.id}`}
        >
          <Trash size={14} weight="bold" />
        </button>
      )}
    </div>
  </div>
);

const AllPapersTab = ({ papers, loading }) => {
  if (loading) return <Loading />;
  if (papers.length === 0) return <Empty message="No papers yet." />;
  return (
    <div className="space-y-2" data-testid="papers-tab">
      {papers.map((p) => (
        <div
          key={p.id}
          className="border-2 border-black bg-white p-3 flex items-center gap-3 flex-wrap"
          data-testid={`admin-paper-${p.id}`}
        >
          <div className="flex-1 min-w-0">
            <div className="font-medium truncate">{p.title || "(untitled)"}</div>
            <div className="text-xs font-mono text-neutral-500 mt-0.5">
              {p.subject} · Class {p.class_name} · {p.total_marks} marks ·{" "}
              <span className="text-neutral-700">{p.owner_email}</span>
            </div>
          </div>
          <span
            className={`qp-badge ${
              p.generation_status === "ready"
                ? "qp-badge-success"
                : ["queued", "pending", "generating", "reviewing"].includes(p.generation_status)
                ? "qp-badge-blue"
                : p.generation_status === "needs_review"
                ? "qp-badge-yellow"
                : "qp-badge-red"
            }`}
          >
            {p.generation_status}
          </span>
          <Link
            to={`/papers/${p.id}`}
            className="qp-btn qp-btn-secondary text-xs"
            data-testid={`admin-view-paper-${p.id}`}
          >
            <Eye size={14} weight="bold" /> View
          </Link>
        </div>
      ))}
    </div>
  );
};

const OperationalOverview = ({ overview }) => {
  const attention = overview.attention || {};
  const attentionCount = (attention.failed_textbooks?.length || 0) + (attention.failed_papers?.length || 0) + (attention.stuck_papers?.length || 0) + (overview.profiles_incomplete || 0);
  return (
    <section className="grid grid-cols-1 lg:grid-cols-3 gap-4 mb-10" data-testid="admin-operational-overview">
      <div className="border-2 border-black bg-white p-5">
        <div className="flex items-center gap-2 font-medium"><ChartBar size={20} weight="bold" /> User pulse</div>
        <div className="grid grid-cols-2 gap-3 mt-4 text-sm">
          <Metric label="New (7 days)" value={overview.new_users_7d} />
          <Metric label="Active (30 days)" value={overview.active_users_30d} />
          <Metric label="Students" value={overview.students} />
          <Metric label="Profiles complete" value={`${overview.profiles_complete}/${overview.users_total}`} />
        </div>
      </div>
      <div className="border-2 border-black bg-white p-5">
        <div className="flex items-center gap-2 font-medium"><GraduationCap size={20} weight="bold" /> School coverage</div>
        <div className="text-3xl font-display mt-3">{overview.schools_total}</div><div className="text-xs font-mono text-neutral-500">schools registered</div>
        <div className="mt-3 flex flex-wrap gap-1">{(overview.board_breakdown || []).map((item) => <span key={item.label} className="qp-badge qp-badge-blue">{item.label} · {item.count}</span>)}</div>
      </div>
      <div className={`border-2 border-black p-5 ${attentionCount ? "bg-[#FFF4C7]" : "bg-white"}`}>
        <div className="flex items-center gap-2 font-medium"><WarningCircle size={20} weight="bold" /> Needs attention</div>
        <div className="text-3xl font-display mt-3">{attentionCount}</div>
        <div className="text-xs font-mono text-neutral-700">{overview.profiles_incomplete} incomplete profiles · {attention.failed_textbooks?.length || 0} failed books · {attention.failed_papers?.length || 0} failed papers · {attention.stuck_papers?.length || 0} stuck papers</div>
      </div>
    </section>
  );
};

const Metric = ({ label, value }) => <div><div className="font-display text-2xl">{value}</div><div className="text-xs font-mono text-neutral-500">{label}</div></div>;

const UsersTab = ({ users, total, filters, onFiltersChange, onOpen, loading }) => {
  if (loading) return <Loading />;
  return (
    <div data-testid="users-tab">
      <div className="grid grid-cols-1 md:grid-cols-6 gap-2 mb-4">
        <label className="md:col-span-2 relative"><MagnifyingGlass size={17} className="absolute left-3 top-3 text-neutral-500" /><input className="qp-input pl-9" value={filters.q} onChange={(e) => onFiltersChange({ ...filters, q: e.target.value })} placeholder="Search name, email, school, board" /></label>
        <select className="qp-input" value={filters.role} onChange={(e) => onFiltersChange({ ...filters, role: e.target.value })}><option value="">All roles</option><option value="teacher">Teachers</option><option value="student">Students</option><option value="admin">Admins</option></select>
        <input className="qp-input" value={filters.board} onChange={(e) => onFiltersChange({ ...filters, board: e.target.value })} placeholder="Filter board" />
        <select className="qp-input" value={filters.profile} onChange={(e) => onFiltersChange({ ...filters, profile: e.target.value })}><option value="">All profiles</option><option value="complete">Complete</option><option value="incomplete">Incomplete</option></select>
        <select className="qp-input" value={filters.activity} onChange={(e) => onFiltersChange({ ...filters, activity: e.target.value })}><option value="">Any activity</option><option value="active">Active in 30 days</option><option value="inactive">Inactive 30+ days</option></select>
      </div>
      <div className="text-xs font-mono text-neutral-500 mb-3">{total} user{total === 1 ? "" : "s"} found</div>
      {users.length === 0 ? <Empty message="No users match these filters." /> : <div className="space-y-2">
      {users.map((u) => (
        <button type="button" onClick={() => onOpen(u.id)}
          key={u.id}
          className="w-full text-left border-2 border-black bg-white p-3 flex items-center gap-3 flex-wrap hover:bg-neutral-50"
          data-testid={`admin-user-${u.id}`}
        >
          <div className="flex-1 min-w-0">
            <div className="font-medium truncate">
              {u.full_name || u.email}{" "}
              <span className="text-xs text-neutral-500 ml-2">{u.email}</span>
            </div>
            <div className="text-xs font-mono text-neutral-500 mt-0.5">
              {u.school_name || "School not provided"} · {u.board === "Other" ? u.board_other_name : u.board || "Board not provided"}
            </div>
            <div className="text-xs font-mono text-neutral-500 mt-0.5">
              {u.textbook_count} textbooks · {u.paper_count} papers · {u.question_count} saved questions · {u.test_count} tests
            </div>
          </div>
          <span
            className={`qp-badge ${
              u.role === "admin" ? "qp-badge-yellow" : "qp-badge-blue"
            }`}
          >
            {u.role}
          </span>
          <div className="text-xs text-neutral-500 flex items-center gap-1"><Clock size={14} /> {formatDate(u.last_active_at) || "Not active yet"}</div>
        </button>
      ))}
      </div>}
    </div>
  );
};

const ActivityTab = ({ activity, loading }) => {
  if (loading) return <Loading />;
  if (!activity.length) return <Empty message="No activity recorded yet." hint="New authenticated and content actions will appear here." />;
  return (
    <div className="space-y-2" data-testid="admin-activity-tab">
      {activity.map((event) => (
        <div key={event.id} className="border-2 border-black bg-white p-3 flex justify-between gap-3 flex-wrap">
          <div>
            <div className="font-medium">{eventLabel(event.event_type)}</div>
            <div className="text-xs font-mono text-neutral-500">{event.user?.full_name || event.user?.email || "Deleted user"} · {event.user?.email || ""}</div>
          </div>
          <div className="text-xs font-mono text-neutral-500">{formatDate(event.occurred_at)}</div>
        </div>
      ))}
    </div>
  );
};

const UserDetailDrawer = ({ detail, onClose }) => {
  const { user, papers, textbooks, questions, tests, activity } = detail;
  return (
    <div className="fixed inset-0 z-50 bg-black/40 flex justify-end" role="dialog" aria-modal="true">
      <aside className="w-full max-w-2xl h-full overflow-y-auto bg-[#FAFAFA] border-l-4 border-black p-6 md:p-8">
        <div className="flex justify-between gap-4 mb-7">
          <div className="min-w-0"><div className="overline text-neutral-500">// User record</div><h2 className="font-display text-4xl mt-2 break-words">{user.full_name || user.email}</h2><div className="text-sm text-neutral-600 break-all">{user.email}</div></div>
          <button type="button" className="qp-btn-ghost h-fit" onClick={onClose} aria-label="Close user details"><X size={20} /></button>
        </div>
        <div className="grid grid-cols-1 min-[420px]:grid-cols-2 md:grid-cols-4 gap-3 mb-6"><Metric label="Textbooks" value={user.textbook_count} /><Metric label="Papers" value={user.paper_count} /><Metric label="Questions" value={user.question_count} /><Metric label="Tests" value={user.test_count} /></div>
        <section className="border-2 border-black bg-white p-4 mb-5"><div className="font-medium mb-2">Profile</div><dl className="grid grid-cols-1 sm:grid-cols-2 gap-3 text-sm"><Detail label="Role" value={user.role} /><Detail label="Provider" value={user.auth_provider} /><Detail label="School" value={user.school_name || "Not provided"} /><Detail label="Board" value={user.board === "Other" ? user.board_other_name : user.board || "Not provided"} /><Detail label="Created" value={formatDate(user.created_at)} /><Detail label="Last active" value={formatDate(user.last_active_at) || "Not active yet"} /></dl></section>
        <ActivitySection title="Recent activity" items={activity} primary={(item) => eventLabel(item.event_type)} secondary={(item) => formatDate(item.occurred_at)} />
        <ActivitySection title="Recent papers" items={papers} primary={(item) => item.title || item.subject || "Untitled paper"} secondary={(item) => `${item.generation_status || "ready"} · ${formatDate(item.created_at)}`} />
        <ActivitySection title="Recent textbooks" items={textbooks} primary={(item) => item.original_filename} secondary={(item) => `${item.status} · ${formatDate(item.created_at)}`} />
        <ActivitySection title="Recent tests" items={tests} primary={(item) => item.status} secondary={(item) => `${item.score?.percent != null ? `${item.score.percent}%` : "Not graded"} · ${formatDate(item.created_at)}`} />
        <ActivitySection title="Saved questions" items={questions} primary={(item) => item.subject || "Question"} secondary={(item) => formatDate(item.created_at)} />
      </aside>
    </div>
  );
};

const Detail = ({ label, value }) => <div><dt className="text-xs font-mono text-neutral-500">{label}</dt><dd>{value}</dd></div>;
const ActivitySection = ({ title, items, primary, secondary }) => (
  <section className="mb-5">
    <h3 className="font-medium mb-2">{title}</h3>
    {items?.length ? <div className="space-y-2">{items.map((item) => <div key={item.id} className="border border-neutral-300 bg-white p-3 text-sm flex flex-col gap-1 sm:flex-row sm:justify-between sm:gap-3"><span className="min-w-0 break-words font-medium">{primary(item)}</span><span className="text-neutral-500 sm:text-right sm:shrink-0">{secondary(item)}</span></div>)}</div> : <div className="text-sm text-neutral-500 border border-dashed border-neutral-400 p-3">None yet.</div>}
  </section>
);
const formatDate = (value) => value ? new Date(value).toLocaleString() : "";
const eventLabel = (value) => (value || "activity").replaceAll("_", " ").replaceAll(".", " · ");

const BulkUploadTab = ({ onDone }) => {
  const [files, setFiles] = useState([]);
  const [subject, setSubject] = useState("");
  const [className, setClassName] = useState("");
  const [shareDefault, setShareDefault] = useState(true);
  const [progress, setProgress] = useState({}); // filename -> "uploading"|"done"|"failed"
  const [running, setRunning] = useState(false);

  const onPick = (list) => {
    const arr = Array.from(list || []).filter((f) =>
      f.name.toLowerCase().endsWith(".pdf")
    );
    if (arr.length === 0) {
      toast.error("Pick one or more PDF files");
      return;
    }
    setFiles(arr);
    setProgress({});
  };

  const start = async () => {
    if (files.length === 0) return toast.error("Pick PDFs first");
    if (!subject || !className)
      return toast.error("Subject & Class required");
    setRunning(true);
    let okCount = 0;
    for (const f of files) {
      setProgress((p) => ({ ...p, [f.name]: "uploading" }));
      try {
        const fd = new FormData();
        fd.append("file", f);
        await api.post(
          `/textbooks/upload?subject=${encodeURIComponent(
            subject
          )}&class_name=${encodeURIComponent(className)}&is_shared=${shareDefault}`,
          fd,
          { headers: { "Content-Type": "multipart/form-data" } }
        );
        setProgress((p) => ({ ...p, [f.name]: "done" }));
        okCount += 1;
      } catch (err) {
        setProgress((p) => ({
          ...p,
          [f.name]: `failed: ${err?.response?.data?.detail || err.message}`,
        }));
      }
    }
    setRunning(false);
    toast.success(`Uploaded ${okCount} of ${files.length}`);
    if (okCount > 0) await onDone();
  };

  return (
    <div className="qp-card max-w-3xl" data-testid="upload-tab">
      <div className="overline mb-3">// Bulk upload</div>
      <p className="text-sm text-neutral-600 font-mono mb-5">
        Drop multiple PDFs at once. Each book will be indexed and (optionally)
        added straight to the shared library so all teachers can use it.
      </p>

      <div className="grid grid-cols-1 md:grid-cols-2 gap-4 mb-4">
        <div>
          <label className="qp-label">Subject (applies to all)</label>
          <input
            value={subject}
            onChange={(e) => setSubject(e.target.value)}
            placeholder="e.g., Physics"
            className="qp-input"
            data-testid="bulk-subject"
          />
        </div>
        <div>
          <label className="qp-label">Class (applies to all)</label>
          <input
            value={className}
            onChange={(e) => setClassName(e.target.value)}
            placeholder="e.g., 10"
            className="qp-input"
            data-testid="bulk-class"
          />
        </div>
      </div>

      <label className="flex items-center gap-2 mb-4 cursor-pointer">
        <input
          type="checkbox"
          checked={shareDefault}
          onChange={(e) => setShareDefault(e.target.checked)}
          data-testid="bulk-share-checkbox"
        />
        <span className="text-sm">
          Add uploaded books to the <b>Shared library</b> automatically
        </span>
      </label>

      <label
        onDragOver={(e) => e.preventDefault()}
        onDrop={(e) => {
          e.preventDefault();
          onPick(e.dataTransfer.files);
        }}
        className="qp-drop"
        data-testid="bulk-drop-zone"
      >
        <UploadSimple size={32} weight="bold" />
        <div className="mt-3 font-medium text-sm">
          {files.length > 0
            ? `${files.length} file(s) selected`
            : "Drop PDFs or click to browse"}
        </div>
        <div className="text-xs text-neutral-500 font-mono mt-1">
          Multiple PDFs · max 500MB each
        </div>
        <input
          type="file"
          accept="application/pdf"
          multiple
          onChange={(e) => onPick(e.target.files)}
          className="hidden"
          data-testid="bulk-file-input"
        />
      </label>

      {files.length > 0 && (
        <div className="mt-4 space-y-1" data-testid="bulk-queue">
          {files.map((f) => {
            const status = progress[f.name];
            return (
              <div
                key={f.name}
                className="flex items-center justify-between text-xs font-mono border border-neutral-200 px-3 py-2"
              >
                <span className="truncate flex-1 mr-2">{f.name}</span>
                <span
                  className={
                    status === "done"
                      ? "text-green-700"
                      : status?.startsWith("failed")
                      ? "text-red-600"
                      : "text-neutral-500"
                  }
                >
                  {status || "queued"}
                </span>
              </div>
            );
          })}
        </div>
      )}

      <button
        onClick={start}
        disabled={running || files.length === 0}
        className="qp-btn qp-btn-primary w-full mt-6"
        data-testid="bulk-upload-start"
      >
        <UploadSimple size={16} weight="bold" />
        {running ? "Uploading..." : `Upload ${files.length || ""} file(s)`}
      </button>
    </div>
  );
};

const Loading = () => (
  <div className="text-sm text-neutral-500 font-mono">Loading…</div>
);
const Empty = ({ message, hint }) => (
  <div className="border-2 border-dashed border-neutral-300 bg-white p-10 text-center">
    <div className="font-display text-2xl">{message}</div>
    {hint && (
      <div className="text-sm text-neutral-500 mt-2 font-mono">{hint}</div>
    )}
  </div>
);
