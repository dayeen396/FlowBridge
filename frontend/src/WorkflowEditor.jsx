import { useCallback, useEffect, useMemo, useState } from "react";
import {
  addEdge,
  Background,
  BackgroundVariant,
  Controls,
  MiniMap,
  ReactFlow,
  useEdgesState,
  useNodesState,
  useReactFlow,
} from "@xyflow/react";
import {
  ArrowLeft,
  Braces,
  Check,
  CircleHelp,
  GitBranch,
  Globe2,
  LoaderCircle,
  Mail,
  Play,
  Plus,
  PlugZap,
  Radio,
  Save,
  Trash2,
  X,
} from "lucide-react";
import { apiRequest, getErrorMessage } from "./api";
import WorkflowNode from "./WorkflowNode";

const nodeTypes = { workflowNode: WorkflowNode };
const nodeIcons = {
  manual_trigger: Play,
  webhook: Radio,
  http_request: Globe2,
  data_processing: Braces,
  condition: GitBranch,
  send_email: Mail,
  mcp_tool: PlugZap,
};

function FitViewOnNodeCount({ count }) {
  const { fitView } = useReactFlow();

  useEffect(() => {
    if (!count) return undefined;
    const timer = window.setTimeout(() => {
      fitView({ padding: 0.15, maxZoom: 1.15 });
    }, 60);
    return () => window.clearTimeout(timer);
  }, [count, fitView]);

  return null;
}

function createId(prefix) {
  return `${prefix}_${crypto.randomUUID().replaceAll("-", "").slice(0, 12)}`;
}

function defaultForSchema(schema) {
  if (Object.hasOwn(schema, "default")) return schema.default;
  if (schema.type === "object") return {};
  if (schema.type === "array") return [];
  if (schema.type === "boolean") return false;
  if (schema.type === "integer" || schema.type === "number") return 0;
  if (schema.enum?.length) return schema.enum[0];
  if (schema.anyOf?.some((choice) => choice.type === "null")) return null;
  return "";
}

function defaultParams(spec) {
  return Object.fromEntries(
    Object.entries(spec.params_schema?.properties ?? {}).map(([key, schema]) => [
      key,
      defaultForSchema(schema),
    ]),
  );
}

function toFlowNodes(definition, catalog) {
  return definition.nodes.map((node) => {
    const spec = catalog.find((entry) => entry.type === node.type);
    return {
      id: node.id,
      type: "workflowNode",
      position: node.position ?? { x: 120, y: 120 },
      data: {
        nodeType: node.type,
        name: node.name,
        params: node.params ?? {},
        retry: node.retry ?? { max_attempts: 1, backoff_seconds: 2, backoff_multiplier: 2 },
        timeoutSeconds: node.timeout_seconds ?? 30,
        onError: node.on_error ?? "stop",
        category: spec?.category ?? "action",
        spec,
      },
    };
  });
}

function toFlowEdges(definition) {
  return definition.edges.map((edge) => ({
    id: edge.id,
    source: edge.source,
    sourceHandle: edge.source_handle ?? "main",
    target: edge.target,
    targetHandle: edge.target_handle ?? "main",
    type: "smoothstep",
    animated: true,
  }));
}

function createDefinition(catalog) {
  const triggerSpec = catalog.find((entry) => entry.type === "manual_trigger");
  return {
    schema_version: 1,
    name: "Untitled workflow",
    description: "",
    nodes: [
      {
        id: "trigger",
        type: "manual_trigger",
        name: "Manual Trigger",
        params: defaultParams(triggerSpec ?? { params_schema: { properties: {} } }),
        position: { x: 340, y: 200 },
        retry: { max_attempts: 1, backoff_seconds: 2, backoff_multiplier: 2 },
        timeout_seconds: 30,
        on_error: "stop",
      },
    ],
    edges: [],
  };
}

function toDefinition(name, description, nodes, edges) {
  return {
    schema_version: 1,
    name: name.trim() || "Untitled workflow",
    description,
    nodes: nodes.map((node) => ({
      id: node.id,
      type: node.data.nodeType,
      name: node.data.name.trim() || node.data.spec?.label || node.data.nodeType,
      params: node.data.params,
      position: { x: node.position.x, y: node.position.y },
      retry: node.data.retry,
      timeout_seconds: node.data.timeoutSeconds,
      on_error: node.data.onError,
    })),
    edges: edges.map((edge) => ({
      id: edge.id,
      source: edge.source,
      source_handle: edge.sourceHandle ?? "main",
      target: edge.target,
      target_handle: edge.targetHandle ?? "main",
    })),
  };
}

function ParamField({ name, schema, value, nodeType, onChange, onValidityChange }) {
  const label = name.replaceAll("_", " ");
  if (schema.enum) {
    return (
      <label className="field">
        <span>{label}</span>
        <select value={value ?? schema.default ?? schema.enum[0]} onChange={(event) => onChange(event.target.value)}>
          {schema.enum.map((option) => <option key={option} value={option}>{option}</option>)}
        </select>
      </label>
    );
  }
  if (schema.type === "boolean") {
    return (
      <label className="toggle-field">
        <span>{label}</span>
        <input type="checkbox" checked={Boolean(value)} onChange={(event) => onChange(event.target.checked)} />
      </label>
    );
  }
  if (schema.type === "object" || schema.type === "array" || (nodeType === "http_request" && name === "body")) {
    return (
      <JsonField
        name={name}
        label={label}
        value={value}
        isArray={schema.type === "array"}
        isAny={schema.type !== "object" && schema.type !== "array"}
        onChange={onChange}
        onValidityChange={onValidityChange}
      />
    );
  }
  if (!schema.type && nodeType === "condition") {
    return (
      <label className="field">
        <span>{label}</span>
        <input
          type="text"
          value={value ?? ""}
          placeholder={name === "left" ? "{{ item.amount }}" : "100"}
          onChange={(event) => {
            const text = event.target.value;
            try {
              onChange(JSON.parse(text));
            } catch {
              onChange(text);
            }
          }}
        />
        {schema.description && <small>{schema.description}</small>}
      </label>
    );
  }
  if (schema.type === "integer" || schema.type === "number") {
    return (
      <label className="field">
        <span>{label}</span>
        <input
          type="number"
          value={value ?? ""}
          min={schema.minimum}
          max={schema.maximum}
          step={schema.type === "integer" ? "1" : "any"}
          onChange={(event) => onChange(event.target.value === "" ? "" : Number(event.target.value))}
        />
      </label>
    );
  }
  return (
    <label className="field">
      <span>{label}</span>
      {name === "body" || name === "subject" ? (
        <textarea rows={name === "body" ? 5 : 2} value={value ?? ""} onChange={(event) => onChange(event.target.value)} />
      ) : (
        <input
          type="text"
          value={value ?? ""}
          placeholder={schema.description?.includes("{{") ? "{{ item.value }}" : ""}
          onChange={(event) => onChange(event.target.value)}
        />
      )}
      {schema.description && <small>{schema.description}</small>}
    </label>
  );
}

function JsonField({ name, label, value, isArray, isAny, onChange, onValidityChange }) {
  const [draft, setDraft] = useState(() => JSON.stringify(value, null, 2));
  const [error, setError] = useState("");

  function validate(text) {
    setDraft(text);
    try {
      const parsed = JSON.parse(text);
      if (isArray && !Array.isArray(parsed)) throw new Error("Expected a JSON array.");
      if (!isArray && (parsed === null || Array.isArray(parsed) || typeof parsed !== "object")) {
        if (!isAny) throw new Error("Expected a JSON object.");
      }
      setError("");
      onValidityChange(name, false);
      onChange(parsed);
    } catch (parseError) {
      setError(parseError instanceof SyntaxError ? "Enter valid JSON." : parseError.message);
      onValidityChange(name, true);
    }
  }

  return (
    <label className={`field json-field ${error ? "has-error" : ""}`}>
      <span>{label} <small>JSON</small></span>
      <textarea
        className="code-input"
        spellCheck="false"
        rows={Math.min(7, Math.max(3, draft.split("\n").length))}
        value={draft}
        onChange={(event) => validate(event.target.value)}
      />
      {error && <small className="field-error">{error}</small>}
    </label>
  );
}

function NodeInspector({ node, onUpdate, onDelete, invalidFields, onValidityChange }) {
  const spec = node.data.spec;
  const properties = spec?.params_schema?.properties ?? {};
  return (
    <aside className="inspector">
      <div className="inspector-header">
        <div>
          <span className="panel-kicker">NODE SETTINGS</span>
          <h2>Configure node</h2>
        </div>
        <button className="icon-button" aria-label="Close settings" onClick={() => onUpdate(null)}><X size={17} /></button>
      </div>
      <div className="inspector-body">
        <div className="node-type-chip">
          <span className={`category-dot ${node.data.category}`} />
          {spec?.label ?? node.data.nodeType}
        </div>
        <label className="field">
          <span>Node name</span>
          <input value={node.data.name} onChange={(event) => onUpdate({ name: event.target.value })} />
        </label>
        <div className="divider-label"><span>Parameters</span></div>
        {Object.entries(properties).map(([name, schema]) => (
          <ParamField
            key={`${node.id}:${name}`}
            name={name}
            schema={schema}
            value={Object.hasOwn(node.data.params, name) ? node.data.params[name] : defaultForSchema(schema)}
            nodeType={node.data.nodeType}
            onChange={(value) => onUpdate({ params: { ...node.data.params, [name]: value } })}
            onValidityChange={(fieldName, invalid) => onValidityChange(`${node.id}:${fieldName}`, invalid)}
          />
        ))}
        {Object.keys(properties).length === 0 && <p className="muted-copy">This node has no configurable parameters.</p>}
        <div className="divider-label"><span>Execution</span></div>
        <label className="field">
          <span>Timeout · seconds</span>
          <input
            type="number"
            min="1"
            max="3600"
            value={node.data.timeoutSeconds}
            onChange={(event) => onUpdate({ timeoutSeconds: Number(event.target.value) })}
          />
        </label>
        <label className="field">
          <span>On error</span>
          <select value={node.data.onError} onChange={(event) => onUpdate({ onError: event.target.value })}>
            <option value="stop">Stop workflow</option>
            <option value="continue">Continue workflow</option>
          </select>
        </label>
        <label className="field">
          <span>Maximum attempts</span>
          <input
            type="number"
            min="1"
            max="10"
            value={node.data.retry.max_attempts}
            onChange={(event) => onUpdate({ retry: { ...node.data.retry, max_attempts: Number(event.target.value) } })}
          />
        </label>
        {invalidFields.length > 0 && <p className="inline-error">Fix invalid JSON fields before saving.</p>}
      </div>
      <div className="inspector-footer">
        <button className="danger-quiet" onClick={onDelete}><Trash2 size={15} /> Delete node</button>
      </div>
    </aside>
  );
}

export default function WorkflowEditor({ workflowId, onBack, onOpenRun, onWorkflowSaved }) {
  const [catalog, setCatalog] = useState([]);
  const [nodes, setNodes, onNodesChange] = useNodesState([]);
  const [edges, setEdges, onEdgesChange] = useEdgesState([]);
  const [name, setName] = useState("Untitled workflow");
  const [description, setDescription] = useState("");
  const [record, setRecord] = useState(null);
  const [selectedId, setSelectedId] = useState(null);
  const [loading, setLoading] = useState(true);
  const [saving, setSaving] = useState(false);
  const [running, setRunning] = useState(false);
  const [dirty, setDirty] = useState(false);
  const [notice, setNotice] = useState(null);
  const [jsonValidity, setJsonValidity] = useState({});
  const [libraryOpen, setLibraryOpen] = useState(false);

  useEffect(() => {
    let active = true;
    async function load() {
      setLoading(true);
      try {
        const [nodeCatalog, saved] = await Promise.all([
          apiRequest("/node-types"),
          workflowId === "new" ? Promise.resolve(null) : apiRequest(`/workflows/${workflowId}`),
        ]);
        if (!active) return;
        setCatalog(nodeCatalog);
        if (saved) {
          setRecord(saved);
          setName(saved.name);
          setDescription(saved.description ?? "");
          setNodes(toFlowNodes(saved.definition, nodeCatalog));
          setEdges(toFlowEdges(saved.definition));
        } else {
          const definition = createDefinition(nodeCatalog);
          setRecord(null);
          setName(definition.name);
          setDescription(definition.description);
          setNodes(toFlowNodes(definition, nodeCatalog));
          setEdges([]);
        }
        setDirty(false);
        setSelectedId(null);
      } catch (error) {
        if (active) setNotice({ kind: "error", text: getErrorMessage(error) });
      } finally {
        if (active) setLoading(false);
      }
    }
    load();
    return () => { active = false; };
  }, [workflowId, setEdges, setNodes]);

  useEffect(() => {
    if (!notice) return undefined;
    const timeout = window.setTimeout(() => setNotice(null), 6000);
    return () => window.clearTimeout(timeout);
  }, [notice]);

  const selectedNode = nodes.find((node) => node.id === selectedId) ?? null;
  const invalidFields = Object.keys(jsonValidity).filter((key) => jsonValidity[key]);
  const groups = useMemo(() => {
    const order = ["trigger", "logic", "action", "integration"];
    return order.map((category) => ({
      category,
      entries: catalog.filter((entry) => entry.category === category),
    })).filter((group) => group.entries.length);
  }, [catalog]);

  const markNodeChanges = useCallback((changes) => {
    const changeList = changes.toArray();
    onNodesChange(changes);
    if (changeList.some((change) => ["position", "add", "remove", "replace"].includes(change.type))) {
      setDirty(true);
    }
  }, [onNodesChange]);

  const markEdgeChanges = useCallback((changes) => {
    const changeList = changes.toArray();
    onEdgesChange(changes);
    if (changeList.some((change) => ["add", "remove", "replace"].includes(change.type))) {
      setDirty(true);
    }
  }, [onEdgesChange]);

  const onConnect = useCallback((connection) => {
    setEdges((current) => addEdge({
      ...connection,
      id: createId("edge"),
      sourceHandle: connection.sourceHandle ?? "main",
      targetHandle: "main",
      type: "smoothstep",
      animated: true,
    }, current));
    setDirty(true);
  }, [setEdges]);

  function addNode(spec) {
    const index = nodes.length;
    const rightmost = nodes.reduce((x, node) => Math.max(x, node.position.x), 0);
    const rowY = nodes[0]?.position.y ?? 200;
    const newNode = {
      id: createId(spec.type),
      type: "workflowNode",
      position: { x: rightmost + 360, y: rowY + Math.floor((index - 1) / 4) * 160 },
      data: {
        nodeType: spec.type,
        name: spec.label,
        params: defaultParams(spec),
        retry: { max_attempts: 1, backoff_seconds: 2, backoff_multiplier: 2 },
        timeoutSeconds: 30,
        onError: "stop",
        category: spec.category,
        spec,
      },
    };
    setNodes((current) => [...current, newNode]);
    setSelectedId(newNode.id);
    setDirty(true);
    setLibraryOpen(false);
  }

  function updateSelected(patch) {
    if (!selectedId) return;
    setNodes((current) => current.map((node) => (
      node.id === selectedId ? { ...node, data: { ...node.data, ...patch } } : node
    )));
    setDirty(true);
  }

  function deleteSelected() {
    if (!selectedId) return;
    setNodes((current) => current.filter((node) => node.id !== selectedId));
    setEdges((current) => current.filter((edge) => edge.source !== selectedId && edge.target !== selectedId));
    setSelectedId(null);
    setJsonValidity({});
    setDirty(true);
  }

  const setFieldValidity = useCallback((name, invalid) => {
    setJsonValidity((current) => ({ ...current, [name]: invalid }));
  }, []);

  async function persistWorkflow() {
    if (invalidFields.length) throw new Error("Fix invalid JSON fields before saving.");
    if (record && !dirty) return record;
    const definition = toDefinition(name, description, nodes, edges);
    setSaving(true);
    try {
      const saved = record
        ? await apiRequest(`/workflows/${record.id}`, {
            method: "PUT",
            body: { ...definition, version: record.version },
          })
        : await apiRequest("/workflows", { method: "POST", body: definition });
      setRecord(saved);
      setName(saved.name);
      setDescription(saved.description ?? "");
      setDirty(false);
      setNotice({ kind: "success", text: "Workflow saved" });
      return saved;
    } catch (error) {
      setNotice({ kind: "error", text: getErrorMessage(error) });
      throw error;
    } finally {
      setSaving(false);
    }
  }

  async function saveWorkflow() {
    try {
      const saved = await persistWorkflow();
      if (workflowId === "new") onWorkflowSaved(saved.id);
    } catch {
      // The validation or API message is already visible in the editor.
    }
  }

  async function runWorkflow() {
    setRunning(true);
    try {
      const saved = await persistWorkflow();
      const queued = await apiRequest(`/workflows/${saved.id}/run`, { method: "POST" });
      onOpenRun(queued.execution_id, saved.id);
    } catch (error) {
      if (error.message !== notice?.text) setNotice({ kind: "error", text: getErrorMessage(error) });
    } finally {
      setRunning(false);
    }
  }

  if (loading) {
    return <div className="center-state"><LoaderCircle className="spin" size={22} /> Loading workflow…</div>;
  }

  return (
    <div className="editor-page">
      <header className="editor-topbar">
        <div className="editor-brand">
          <button className="icon-button back-button" onClick={onBack} aria-label="Back to workflows"><ArrowLeft size={18} /></button>
          <span className="brand-mark small">F</span>
          <span className="brand-word">flow<span>bridge</span></span>
          <span className="crumb-separator">/</span>
          <span className="crumb-current">Editor</span>
        </div>
        <div className="editor-title">
          <input aria-label="Workflow name" value={name} onChange={(event) => { setName(event.target.value); setDirty(true); }} />
          <input className="editor-description" aria-label="Workflow description" placeholder="Add a short description…" value={description} onChange={(event) => { setDescription(event.target.value); setDirty(true); }} />
        </div>
        <div className="editor-save-state">
          <span className={`save-indicator ${dirty ? "unsaved" : ""}`}>
            <span /> {dirty ? "Unsaved changes" : record ? `Saved · v${record.version}` : "New workflow"}
          </span>
        </div>
        <div className="editor-actions">
          <button className="button secondary save-button" onClick={saveWorkflow} disabled={saving || invalidFields.length > 0}>
            {saving ? <LoaderCircle className="spin" size={16} /> : <Save size={16} />}
            <span>{saving ? "Saving" : "Save"}</span>
          </button>
          <button className="button primary run-button" onClick={runWorkflow} disabled={running || saving || invalidFields.length > 0}>
            {running ? <LoaderCircle className="spin" size={16} /> : <Play size={15} fill="currentColor" />}
            <span>{running ? "Queueing…" : "Run workflow"}</span>
          </button>
        </div>
      </header>

      <div className={`editor-workspace ${selectedNode ? "with-inspector" : ""}`}>
        <aside className="node-library">
          <div className="library-heading">
            <div><span className="panel-kicker">BUILD</span><h2>Node library</h2></div>
            <button className="icon-button mobile-library-close" aria-label="Close node library" onClick={() => setLibraryOpen(false)}><X size={16} /></button>
          </div>
          <p className="library-copy">Add a step to your workflow, then connect it on the canvas.</p>
          {groups.map((group) => (
            <section className="library-group" key={group.category}>
              <h3>{group.category}</h3>
              {group.entries.map((spec) => {
                const Icon = nodeIcons[spec.type] ?? CircleHelp;
                return (
                  <button className="library-node" key={spec.type} onClick={() => addNode(spec)}>
                    <span className={`library-node-icon ${group.category}`}><Icon size={16} /></span>
                    <span className="library-node-label">{spec.label}<small>{spec.description}</small></span>
                    <Plus className="library-add" size={14} />
                  </button>
                );
              })}
            </section>
          ))}
          <div className="library-tip"><CircleHelp size={15} /><span>Connect the trigger to every step. Branches can use true / false handles.</span></div>
        </aside>

        <main className="canvas-area">
          <div className="canvas-toolbar">
            <button className="canvas-tool active"><span className="canvas-tool-dot" /> Workflow</button>
            <div className="canvas-toolbar-right">
              <span className="canvas-count">{nodes.length} nodes <span>·</span> {edges.length} connections</span>
              <button className="button secondary compact add-node-mobile" onClick={() => setLibraryOpen(true)}><Plus size={15} /> Add node</button>
            </div>
          </div>
          <div className="canvas-inner">
            <ReactFlow
              className="light"
              nodes={nodes}
              edges={edges}
              nodeTypes={nodeTypes}
              onNodesChange={markNodeChanges}
              onEdgesChange={markEdgeChanges}
              onConnect={onConnect}
              onNodeClick={(_event, node) => setSelectedId(node.id)}
              onPaneClick={() => setSelectedId(null)}
              deleteKeyCode={["Backspace", "Delete"]}
              fitView
              fitViewOptions={{ padding: 0.15, maxZoom: 1.15 }}
              minZoom={0.25}
              maxZoom={1.5}
              defaultEdgeOptions={{ type: "smoothstep", animated: true }}
            >
              <FitViewOnNodeCount count={nodes.length} />
              <Background variant={BackgroundVariant.Dots} gap={22} size={1} color="#cbd5e1" />
              <Controls position="bottom-left" showInteractive={false} />
              <MiniMap
                position="top-right"
                pannable
                zoomable
                nodeColor={(node) => node.data?.category === "trigger" ? "#8b5cf6" : node.data?.category === "logic" ? "#10b981" : "#3b82f6"}
              />
            </ReactFlow>
            {nodes.length === 1 && edges.length === 0 && (
              <div className="canvas-hint"><span className="hint-line" /> Add a node and connect it to start building</div>
            )}
          </div>
        </main>

        {selectedNode && (
          <NodeInspector
            node={selectedNode}
            onUpdate={(patch) => patch === null ? setSelectedId(null) : updateSelected(patch)}
            onDelete={deleteSelected}
            invalidFields={invalidFields}
            onValidityChange={setFieldValidity}
          />
        )}
      </div>
      {libraryOpen && (
        <div className="library-mobile-backdrop" onClick={() => setLibraryOpen(false)}>
          <div className="library-mobile-sheet" role="dialog" aria-modal="true" aria-label="Add a workflow node" onClick={(event) => event.stopPropagation()}>
            <div className="library-heading">
              <div><span className="panel-kicker">BUILD</span><h2>Add a step</h2></div>
              <button className="icon-button" aria-label="Close node picker" onClick={() => setLibraryOpen(false)}><X size={16} /></button>
            </div>
            <p className="library-copy">Choose a node to add it to the canvas.</p>
            {groups.map((group) => (
              <section className="library-group" key={group.category}>
                <h3>{group.category}</h3>
                {group.entries.map((spec) => {
                  const Icon = nodeIcons[spec.type] ?? CircleHelp;
                  return (
                    <button className="library-node" key={spec.type} onClick={() => addNode(spec)}>
                      <span className={`library-node-icon ${group.category}`}><Icon size={16} /></span>
                      <span className="library-node-label">{spec.label}<small>{spec.description}</small></span>
                      <Plus className="library-add" size={14} />
                    </button>
                  );
                })}
              </section>
            ))}
          </div>
        </div>
      )}
      {notice && <div className={`toast ${notice.kind}`} role="status">{notice.kind === "success" ? <Check size={16} /> : <CircleHelp size={16} />}{notice.text}</div>}
    </div>
  );
}
