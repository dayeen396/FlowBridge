import { memo } from "react";
import { Handle, Position } from "@xyflow/react";
import {
  Braces,
  GitBranch,
  Globe2,
  Mail,
  Play,
  PlugZap,
  Radio,
} from "lucide-react";

const icons = {
  manual_trigger: Play,
  webhook: Radio,
  http_request: Globe2,
  data_processing: Braces,
  condition: GitBranch,
  send_email: Mail,
  mcp_tool: PlugZap,
};

function WorkflowNode({ data, selected }) {
  const Icon = icons[data.nodeType] ?? Braces;
  const summary =
    data.nodeType === "http_request"
      ? `${data.params?.method ?? "GET"} · ${data.params?.url || "Add a URL"}`
      : data.nodeType === "data_processing"
        ? `${data.params?.operation ?? "set"} fields`
        : data.nodeType === "condition"
          ? `${data.params?.operator ?? "equals"} condition`
          : data.spec?.description ?? "Configure this node";
  const outputs = data.spec?.outputs ?? ["main"];

  return (
    <article className={`flow-node ${data.category ?? "action"} ${selected ? "selected" : ""}`}>
      {data.category !== "trigger" && (
        <Handle className="flow-handle target-handle" type="target" position={Position.Left} id="main" />
      )}
      <div className="flow-node-top">
        <span className="flow-node-icon"><Icon size={15} strokeWidth={2.2} /></span>
        <span className="flow-node-type">{data.spec?.label ?? data.nodeType}</span>
        {data.category === "trigger" && <span className="trigger-pill">START</span>}
      </div>
      <div className="flow-node-name">{data.name || "Untitled node"}</div>
      <div className="flow-node-summary" title={summary}>{summary}</div>
      <div className="flow-node-footer">
        <span>{outputs.length > 1 ? `${outputs.length} branches` : "1 output"}</span>
        {outputs.length > 1 && <span className="branch-labels">true · false</span>}
      </div>
      {outputs.map((output, index) => (
        <Handle
          key={output}
          className={`flow-handle source-handle ${output !== "main" ? `handle-${output}` : ""}`}
          type="source"
          position={Position.Right}
          id={output}
          style={outputs.length > 1 ? { top: `${62 + index * 22}%` } : undefined}
        />
      ))}
    </article>
  );
}

export default memo(WorkflowNode);
