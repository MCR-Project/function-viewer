import { Item } from "./models";

const scoreOf = (item: Item) => item.weight * 2;

/** Orders two items, heaviest first. */
export function compare(a: Item, b: Item): number {
  return a.heavier(b) ? -1 : 1;
}

/** Scores every item, then sorts them by weight. */
export function processAll(items: Item[]) {
  const sorted = [...items].sort((a, b) => compare(a, b));
  return sorted.map((item) => ({ item, score: scoreOf(item) }));
}
