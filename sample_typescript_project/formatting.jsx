function label(result) {
  return `${result.item.name}: ${result.score}`;
}

/** Renders one result as a list item. */
export function renderRow(result) {
  return <li key={result.item.name}>{label(result)}</li>;
}
