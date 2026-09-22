import { renderRow } from "./formatting";

export default function (results: { item: { name: string }; score: number }[]) {
  return <ul>{results.map((result) => renderRow(result))}</ul>;
}
