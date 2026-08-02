export interface ParamInfo {
  name: string;
  annotation: string | null;
  default: string | null;
}

export interface CodeLine {
  lineno: number;
  text: string;
  calls: string[];
}

export interface FunctionInfo {
  id: string;
  name: string;
  qualname: string;
  file: string;
  module: string;
  className: string | null;
  lineno: number;
  endLineno: number;
  params: ParamInfo[];
  returns: string | null;
  docstring: string | null;
  isAsync: boolean;
  codeLines: CodeLine[];
  calls: string[];
  language: string;
}

export interface FileInfo {
  path: string;
  functions: string[];
  error: string | null;
  language: string | null;
}

export interface CallEdge {
  source: string;
  target: string;
  line: number;
}

export interface Graph {
  root: string;
  files: FileInfo[];
  functions: Record<string, FunctionInfo>;
  edges: CallEdge[];
}
