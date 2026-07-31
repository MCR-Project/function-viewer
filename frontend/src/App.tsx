import { ReactFlowProvider } from "@xyflow/react";
import { GraphCanvas } from "./components/GraphCanvas";
import { Sidebar } from "./components/Sidebar";

export default function App() {
  return (
    <ReactFlowProvider>
      <div className="app">
        <Sidebar />
        <GraphCanvas />
      </div>
    </ReactFlowProvider>
  );
}
