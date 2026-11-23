import { useCallback, useEffect, useState } from "react";
import {
  ArrowUpRight,
  ChevronRight,
  CircleHelp,
  Clock3,
  LogOut,
  Plus,
  Search,
  Workflow as WorkflowIcon,
} from "lucide-react";
import { apiRequest, getErrorMessage } from "./api";
import RunPage from "./RunPage";
import WorkflowEditor from "./WorkflowEditor";

function routeFromHash() {
  const segments = window.location.hash.replace(/^#\/?/, "").split("/").filter(Boolean);
  if (segments[0] === "editor" && segments[1]) {
    return { page: "editor", workflowId: segments[1] };
  }
  if (segments[0] === "runs" && segments[1]) {
    return { page: "run", executionId: segments[1], workflowId: segments[2] ?? "" };
  }
  return { page: "home" };
}

function formatUpdated(value) {
  if (!value) return "Just created";
  const date = new Date(value);
  return `Edited ${date.toLocaleDateString([], { month: "short", day: "numeric" })}`;
}

function LoginScreen({ loading, error }) {
  const unavailable = error && error.status !== 401;
  return (
    <main className="login-screen">
      <div className="login-orbit orbit-one" />
      <div className="login-orbit orbit-two" />
      <section className="login-card">
        <div className="brand-mark login-mark">F</div>
        <p className="eyebrow">YOUR AUTOMATION STUDIO</p>
        <h1>Make work<br /><span>flow forward.</span></h1>
        <p className="login-description">Design connected workflows, run them in the background, and keep every step in your control.</p>
        {loading ? (
          <div className="login-loading"><span className="loader-dot" /> Checking your workspace</div>
        ) : (
          <a className="github-button" href="/api/auth/github/login">
            <GitHubMark /> Continue with GitHub <ArrowUpRight size={16} />
          </a>
        )}
        {unavailable && <div className="login-error" role="alert">{getErrorMessage(error)}</div>}
        <div className="login-footnote"><span className="secure-dot" /> Self-hosted · Your workflows stay on your server</div>
      </section>
      <div className="login-decoration"><span>FLOWBRIDGE</span><span>Build with clarity.</span></div>
    </main>
  );
}

function GitHubMark() {
  return (
    <svg viewBox="0 0 24 24" aria-hidden="true">
      <path fill="currentColor" d="M12 .9a11.1 11.1 0 0 0-3.51 21.63c.56.1.76-.24.76-.54v-2.1c-3.1.68-3.76-1.32-3.76-1.32-.5-1.29-1.24-1.64-1.24-1.64-1.01-.7.08-.69.08-.69 1.12.08 1.71 1.15 1.71 1.15 1 1.7 2.61 1.21 3.25.93.1-.73.39-1.22.71-1.5-2.48-.28-5.09-1.24-5.09-5.52 0-1.22.44-2.22 1.15-3-.12-.28-.5-1.42.11-2.96 0 0 .94-.3 3.05 1.14a10.6 10.6 0 0 1 5.55 0c2.11-1.44 3.04-1.14 3.04-1.14.61 1.54.23 2.68.12 2.96.71.78 1.14 1.78 1.14 3.01 0 4.29-2.61 5.23-5.1 5.5.4.35.76 1.03.76 2.08v3.1c0 .3.2.65.77.54A11.1 11.1 0 0 0 12 .9Z" />
    </svg>
  );
}

function WorkflowCard({ workflow, onOpen, onDelete }) {
  const initials = workflow.name.slice(0, 1).toUpperCase();
  return (
    <article className="workflow-card" onClick={onOpen} onKeyDown={(event) => {
      if (event.key === "Enter" || event.key === " ") onOpen();
    }} role="button" tabIndex={0}>
      <div className={`workflow-card-symbol symbol-${workflow.id.slice(-1)}`}><WorkflowIcon size={18} /></div>
      <div className="workflow-card-main">
        <h3>{workflow.name}</h3>
        <p>{workflow.description || "A clean canvas ready for your next automation."}</p>
        <div className="workflow-card-meta">
          <span><span className="tiny-green-dot" /> Draft</span>
          <span>{formatUpdated(workflow.updated_at)}</span>
          <span>v{workflow.version}</span>
        </div>
      </div>
      <button
        className="workflow-card-delete"
        aria-label={`Delete ${workflow.name}`}
        onClick={(event) => { event.stopPropagation(); onDelete(); }}
      >
        <span>Delete</span>
      </button>
      <ChevronRight className="workflow-card-arrow" size={18} />
      <span className="sr-only">{initials}</span>
    </article>
  );
}

function HomePage({ user, onCreate, onOpen, onLogout, refreshKey }) {
  const [workflows, setWorkflows] = useState([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState("");
  const [query, setQuery] = useState("");

  const loadWorkflows = useCallback(async () => {
    setLoading(true);
    try {
      const result = await apiRequest("/workflows?limit=100");
      setWorkflows(result.items);
      setError("");
    } catch (requestError) {
      setError(getErrorMessage(requestError));
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => { loadWorkflows(); }, [loadWorkflows, refreshKey]);

  async function deleteWorkflow(workflow) {
    if (!window.confirm(`Delete "${workflow.name}" and its execution history?`)) return;
    try {
      await apiRequest(`/workflows/${workflow.id}`, { method: "DELETE" });
      setWorkflows((current) => current.filter((entry) => entry.id !== workflow.id));
    } catch (deleteError) {
      setError(getErrorMessage(deleteError));
    }
  }

  const filtered = workflows.filter((workflow) => workflow.name.toLowerCase().includes(query.toLowerCase()));
  return (
    <div className="home-shell">
      <aside className="app-sidebar">
        <a className="sidebar-brand" href="#/">
          <span className="brand-mark small">F</span><span className="brand-word">flow<span>bridge</span></span>
        </a>
        <div className="workspace-switcher">
          <div className="workspace-avatar">{(user.name || user.login).slice(0, 1).toUpperCase()}</div>
          <div><strong>{user.name || user.login}</strong><small>Personal workspace</small></div>
          <ChevronRight size={14} />
        </div>
        <div className="sidebar-section-label">WORKSPACE</div>
        <button className="sidebar-nav active"><WorkflowIcon size={17} /> Workflows <span>{workflows.length}</span></button>
        <button className="sidebar-nav disabled" title="Execution history is available per workflow"><Clock3 size={17} /> Activity</button>
        <div className="sidebar-bottom">
          <div className="sidebar-help"><CircleHelp size={17} /><span><strong>Need a hand?</strong><small>Read the quick-start guide</small></span><ArrowUpRight size={14} /></div>
          <button className="sidebar-user" onClick={onLogout}>
            {user.avatar_url ? <img src={user.avatar_url} alt="" /> : <span className="user-avatar">{(user.name || user.login).slice(0, 1).toUpperCase()}</span>}
            <span><strong>{user.name || user.login}</strong><small>@{user.login}</small></span>
            <LogOut size={15} />
          </button>
        </div>
      </aside>

      <main className="home-main">
        <header className="home-topbar">
          <div className="breadcrumb"><span>Workspace</span><ChevronRight size={14} /><strong>Workflows</strong></div>
          <div className="api-indicator"><span className="api-indicator-dot" /> API connected</div>
        </header>
        <div className="home-content">
          <div className="welcome-strip">
            <div className="welcome-copy">
              <span className="eyebrow">MONDAY, YOUR WAY</span>
              <h1>Good work starts<br />with a <em>clear flow.</em></h1>
              <p>Bring your repetitive tasks together. Build once, run whenever you need.</p>
              <button className="button primary welcome-create" onClick={onCreate}><Plus size={17} /> Create a workflow</button>
            </div>
            <div className="welcome-art" aria-hidden="true">
              <div className="art-orbit orbit-a" /><div className="art-orbit orbit-b" />
              <div className="art-node art-trigger"><span>01</span><b>Trigger</b></div>
              <div className="art-link link-a" />
              <div className="art-node art-process"><span>02</span><b>Process</b></div>
              <div className="art-link link-b" />
              <div className="art-node art-result"><span>03</span><b>Action</b></div>
              <span className="art-spark spark-one">✳</span><span className="art-spark spark-two">✦</span>
            </div>
          </div>

          <section className="workflow-section">
            <div className="section-title-row">
              <div><span className="panel-kicker">YOUR WORKSPACE</span><h2>All workflows <span>{workflows.length}</span></h2></div>
              <label className="workflow-search"><Search size={16} /><input placeholder="Find a workflow" value={query} onChange={(event) => setQuery(event.target.value)} /></label>
            </div>
            {error && <div className="page-alert">{error}</div>}
            {loading ? (
              <div className="dashboard-loading"><span className="loader-dot" /> Loading your workflows…</div>
            ) : filtered.length ? (
              <div className="workflow-list">
                {filtered.map((workflow) => (
                  <WorkflowCard
                    key={workflow.id}
                    workflow={workflow}
                    onOpen={() => onOpen(workflow.id)}
                    onDelete={() => deleteWorkflow(workflow)}
                  />
                ))}
              </div>
            ) : workflows.length ? (
              <div className="empty-search">No workflows match “{query}”.</div>
            ) : (
              <div className="empty-workflows">
                <div className="empty-icon"><WorkflowIcon size={22} /></div>
                <h3>Your next great flow starts here.</h3>
                <p>Add a trigger, connect a few steps, and let FlowBridge handle the repeat work.</p>
                <button className="button secondary" onClick={onCreate}><Plus size={16} /> Create your first workflow</button>
              </div>
            )}
          </section>
          <footer className="home-footer"><span>FLOWBRIDGE <i>·</i> SELF-HOSTED AUTOMATION</span><span>Built to keep your work moving.</span></footer>
        </div>
      </main>
    </div>
  );
}

export default function App() {
  const [user, setUser] = useState(null);
  const [authLoading, setAuthLoading] = useState(true);
  const [authError, setAuthError] = useState(null);
  const [route, setRoute] = useState(routeFromHash);
  const [refreshKey, setRefreshKey] = useState(0);

  useEffect(() => {
    const changeRoute = () => setRoute(routeFromHash());
    window.addEventListener("hashchange", changeRoute);
    return () => window.removeEventListener("hashchange", changeRoute);
  }, []);

  useEffect(() => {
    let active = true;
    apiRequest("/me")
      .then((currentUser) => { if (active) setUser(currentUser); })
      .catch((error) => { if (active) setAuthError(error); })
      .finally(() => { if (active) setAuthLoading(false); });
    return () => { active = false; };
  }, []);

  function navigate(hash) {
    window.location.hash = hash;
  }

  async function logout() {
    try {
      await apiRequest("/auth/logout", { method: "POST" });
      setAuthError(null);
    } catch (error) {
      setAuthError(error);
    }
    setUser(null);
    navigate("/");
  }

  if (authLoading) {
    return <LoginScreen loading error={authError} />;
  }
  if (!user) return <LoginScreen loading={false} error={authError} />;

  if (route.page === "editor") {
    return (
      <WorkflowEditor
        key={route.workflowId}
        workflowId={route.workflowId}
        onBack={() => { setRefreshKey((value) => value + 1); navigate("/"); }}
        onWorkflowSaved={(id) => { setRefreshKey((value) => value + 1); navigate(`/editor/${id}`); }}
        onOpenRun={(executionId, workflowId) => navigate(`/runs/${executionId}/${workflowId}`)}
      />
    );
  }
  if (route.page === "run") {
    return (
      <RunPage
        executionId={route.executionId}
        onBack={() => navigate(route.workflowId ? `/editor/${route.workflowId}` : "/")}
        onOpenWorkflow={(workflowId) => workflowId && navigate(`/editor/${workflowId}`)}
      />
    );
  }
  return (
    <HomePage
      user={user}
      refreshKey={refreshKey}
      onCreate={() => navigate("/editor/new")}
      onOpen={(id) => navigate(`/editor/${id}`)}
      onLogout={logout}
    />
  );
}
