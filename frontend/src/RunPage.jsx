import { useEffect, useMemo, useState } from "react";
import {
  AlertCircle,
  ArrowLeft,
  Braces,
  CheckCircle2,
  CircleDashed,
  Clock3,
  GitBranch,
  Globe2,
  LoaderCircle,
  Play,
  RefreshCw,
  SkipForward,
  XCircle,
} from "lucide-react";
import { apiRequest, getErrorMessage } from "./api";

const nodeIcons = {
  manual_trigger: Play,
  webhook: CircleDashed,
  http_request: Globe2,
  data_processing: Braces,
  condition: GitBranch,
};

const terminal = new Set(["success", "failed", "cancelled"]);

function StatusIcon({ status }) {
  if (status === "success") return <CheckCircle2 size={17} />;
  if (status === "failed") return <XCircle size={17} />;
  if (status === "skipped") return <SkipForward size={17} />;
  if (status === "running") return <LoaderCircle className="spin" size={17} />;
  return <CircleDashed size={17} />;
}

function formatTime(value) {
  if (!value) return "—";
  return new Date(value).toLocaleString([], { dateStyle: "medium", timeStyle: "short" });
}

function formatDuration(value) {
  if (value == null) return "—";
  return value < 1000 ? `${value} ms` : `${(value / 1000).toFixed(2)} s`;
}

function NodeRunCard({ run, attempts }) {
  const Icon = nodeIcons[run.node_type] ?? Braces;
  const latest = attempts[attempts.length - 1];
  return (
    <article className={`run-node-card ${latest.status}`}>
      <div className="run-node-heading">
        <div className={`run-node-icon ${latest.status}`}><Icon size={16} /></div>
        <div className="run-node-title">
          <strong>{run.node_name}</strong>
          <span>{run.node_type.replaceAll("_", " ")}</span>
        </div>
        <span className={`run-status ${latest.status}`}><StatusIcon status={latest.status} />{latest.status}</span>
      </div>
      {attempts.map((attempt) => (
        <section className="attempt-block" key={attempt.id}>
          <div className="attempt-label">
            <span>Attempt {attempt.attempt}</span>
            {attempt.duration_ms != null && <span>{formatDuration(attempt.duration_ms)}</span>}
          </div>
          {attempt.error && (
            <div className="node-error">
              <AlertCircle size={15} />
              <div><strong>{attempt.error.code}</strong><p>{attempt.error.message}</p></div>
            </div>
          )}
          {attempt.output && Object.keys(attempt.output).length > 0 && (
            <div className="node-output">
              <div className="output-label"><span>OUTPUT</span><span>{Object.keys(attempt.output).join(", ")}</span></div>
              <pre>{JSON.stringify(attempt.output, null, 2)}</pre>
            </div>
          )}
        </section>
      ))}
      {latest.status === "skipped" && <p className="skipped-note">No active input reached this node.</p>}
    </article>
  );
}

export default function RunPage({ executionId, onBack, onOpenWorkflow }) {
  const [execution, setExecution] = useState(null);
  const [error, setError] = useState("");
  const [lastUpdated, setLastUpdated] = useState(null);

  useEffect(() => {
    let active = true;
    let timer;
    async function poll() {
      try {
        const current = await apiRequest(`/executions/${executionId}`);
        if (!active) return;
        setExecution(current);
        setError("");
        setLastUpdated(new Date());
        if (!terminal.has(current.status)) timer = window.setTimeout(poll, 2000);
      } catch (requestError) {
        if (!active) return;
        setError(getErrorMessage(requestError));
        timer = window.setTimeout(poll, 2000);
      }
    }
    poll();
    return () => {
      active = false;
      window.clearTimeout(timer);
    };
  }, [executionId]);

  const groupedRuns = useMemo(() => {
    const groups = new Map();
    for (const run of execution?.node_runs ?? []) {
      if (!groups.has(run.node_id)) groups.set(run.node_id, []);
      groups.get(run.node_id).push(run);
    }
    return [...groups.entries()].map(([nodeId, attempts]) => ({
      nodeId,
      attempts,
      run: attempts[0],
    }));
  }, [execution?.node_runs]);

  return (
    <div className="run-page">
      <header className="run-topbar">
        <div className="editor-brand">
          <button className="icon-button back-button" onClick={onBack} aria-label="Back to workflows"><ArrowLeft size={18} /></button>
          <span className="brand-mark small">F</span>
          <span className="brand-word">flow<span>bridge</span></span>
          <span className="crumb-separator">/</span>
          <span className="crumb-current">Run details</span>
        </div>
        {execution && (
          <div className={`execution-status ${execution.status}`}>
            <StatusIcon status={execution.status} />
            {execution.status}
          </div>
        )}
      </header>

      <main className="run-content">
        <div className="run-page-heading">
          <div>
            <button className="text-back" onClick={() => onOpenWorkflow(execution?.workflow_id)}><ArrowLeft size={14} /> Workflow</button>
            <p className="panel-kicker">EXECUTION</p>
            <h1>Run details</h1>
            <p className="run-id">{executionId}</p>
          </div>
          {execution && (
            <div className="run-meta">
              <span><Clock3 size={15} /> Started {formatTime(execution.started_at ?? execution.queued_at)}</span>
              <span><RefreshCw size={14} className={!terminal.has(execution.status) ? "spin-slow" : ""} />
                {terminal.has(execution.status) ? `Updated ${formatTime(lastUpdated)}` : "Refreshing every 2 seconds"}
              </span>
            </div>
          )}
        </div>

        {error && <div className="page-alert"><AlertCircle size={16} />{error}</div>}
        {execution?.error && (
          <div className="execution-error"><AlertCircle size={18} /><div><strong>{execution.error.code}</strong><p>{execution.error.message}</p></div></div>
        )}
        <section className="run-results">
          <div className="run-section-heading">
            <div><h2>Node activity</h2><p>Outputs and errors from each workflow step</p></div>
            {execution && <span>{groupedRuns.length} nodes</span>}
          </div>
          {!execution && !error && <div className="run-loading"><LoaderCircle className="spin" size={19} /> Loading execution…</div>}
          {execution && groupedRuns.length === 0 && (
            <div className="run-waiting">
              <span className={`waiting-icon ${execution.status}`}><LoaderCircle className={terminal.has(execution.status) ? "" : "spin"} size={20} /></span>
              <strong>{terminal.has(execution.status) ? "No node results were recorded" : "Waiting for the worker"}</strong>
              <p>{terminal.has(execution.status) ? "The execution finished without node-level output." : "This page updates automatically as soon as the run completes."}</p>
            </div>
          )}
          <div className="run-node-list">
            {groupedRuns.map(({ nodeId, run, attempts }) => <NodeRunCard key={nodeId} run={run} attempts={attempts} />)}
          </div>
        </section>
      </main>
    </div>
  );
}
