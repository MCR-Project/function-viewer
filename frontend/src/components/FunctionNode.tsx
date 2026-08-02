import { memo, useMemo, useState } from "react";
import { Handle, Position, type NodeProps, type Node } from "@xyflow/react";
import Prism from "prismjs";
import "prismjs/components/prism-python";
import "prismjs/components/prism-rust";
import { fileColor } from "../colors";
import { useViewer } from "../store";
import type { FunctionInfo } from "../types";

export type FunctionNodeType = Node<{ fn: FunctionInfo }, "function">;

const DOC_SECTION = /^(Args|Arguments|Returns|Yields|Raises|Attributes|Examples?|Notes?|Parameters):\s*$/;

function Docstring({ text }: { text: string }) {
  const [expanded, setExpanded] = useState(false);
  const lines = text.split("\n");
  const summary = lines[0];
  const rest = lines.slice(1).join("\n").trim();

  return (
    <div className="fn-doc" onClick={() => rest && setExpanded((v) => !v)} title={rest ? "Toggle full docstring" : undefined}>
      <div className="doc-summary">{summary}</div>
      {expanded && rest && (
        <div className="doc-rest">
          {rest.split("\n").map((line, i) =>
            DOC_SECTION.test(line.trim()) ? (
              <div key={i} className="doc-section">
                {line}
              </div>
            ) : (
              <div key={i}>{line || " "}</div>
            ),
          )}
        </div>
      )}
      {!expanded && rest && <div className="doc-more">··· click to expand</div>}
    </div>
  );
}

function Signature({ fn }: { fn: FunctionInfo }) {
  return (
    <span className="fn-sig">
      (
      {fn.params.map((p, i) => (
        <span key={p.name}>
          {i > 0 && ", "}
          <span className="p-name">{p.name}</span>
          {p.annotation && <span className="p-ann">: {p.annotation}</span>}
          {p.default && <span className="p-ann"> = {p.default}</span>}
        </span>
      ))}
      ) <span className="p-ret">→ {fn.returns ?? "None"}</span>
    </span>
  );
}

function FunctionNodeInner({ data, selected }: NodeProps<FunctionNodeType>) {
  const { fn } = data;
  const isActive = useViewer((s) => s.activeIds.has(fn.id));
  const toggle = useViewer((s) => s.toggle);
  const showFlow = useViewer((s) => s.showFlow);

  const color = fileColor(fn.file);

  const highlighted = useMemo(() => {
    const grammar = Prism.languages[fn.language] ?? Prism.languages.python;
    return fn.codeLines.map((line) => Prism.highlight(line.text, grammar, fn.language));
  }, [fn.codeLines, fn.language]);

  const dotTitle = isActive ? "Active, click to disable" : "Inactive, click to enable";

  return (
    <div
      className={`fn-card ${isActive ? "active" : "inactive"} ${selected ? "selected" : ""}`}
      style={{ "--cat": color } as React.CSSProperties}
    >
      <div className="fn-header">
        <Handle type="target" position={Position.Left} id="in" className="pin pin-in" isConnectable={false} />
        <button className={`status-dot ${isActive ? "on" : "off"}`} title={dotTitle} onClick={() => toggle(fn.id)} />
        <div className="fn-title">
          <div className="fn-name">
            {fn.isAsync && <span className="async-badge">ASYNC</span>}
            {fn.qualname}
          </div>
          <Signature fn={fn} />
        </div>
        <button
          className="flow-btn"
          title="Show everything that leads to this function"
          onClick={() => showFlow(fn.id, "up")}
        >
          ▲ FLOW
        </button>
        <button
          className="flow-btn"
          title="Show everything this function leads to"
          onClick={() => showFlow(fn.id, "down")}
        >
          ▼ FLOW
        </button>
      </div>

      {fn.docstring && <Docstring text={fn.docstring} />}

      <div className="fn-code nodrag nowheel">
        {fn.codeLines.map((line, i) => (
          <div key={line.lineno} className={`code-line ${line.calls.length > 0 ? "has-call" : ""}`}>
            <span className="ln">{line.lineno}</span>
            <pre dangerouslySetInnerHTML={{ __html: highlighted[i] || " " }} />
            {line.calls.length > 0 && (
              <Handle
                type="source"
                position={Position.Right}
                id={`L${line.lineno}`}
                className="pin pin-out"
                isConnectable={false}
              />
            )}
          </div>
        ))}
      </div>
    </div>
  );
}

export const FunctionNode = memo(FunctionNodeInner);
