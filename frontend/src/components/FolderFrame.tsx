import { memo } from "react";
import type { Node, NodeProps } from "@xyflow/react";
import { fileColor } from "../colors";

export type FolderFrameType = Node<{ file: string; width: number; height: number }, "folder">;

function FolderFrameInner({ data }: NodeProps<FolderFrameType>) {
  const color = fileColor(data.file);
  return (
    <div
      className="folder-frame"
      style={{ width: data.width, height: data.height, "--folder-color": color } as React.CSSProperties}
    >
      <div className="folder-tab">{data.file}</div>
      <div className="folder-body" />
    </div>
  );
}

export const FolderFrame = memo(FolderFrameInner);
