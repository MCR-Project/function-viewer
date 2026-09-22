import { createItem, Item } from "./models";
import { processAll } from "./processing";
import { save, load } from "./storage";
import Report from "./Report";

/** Entry point: build a few items, process them, persist them and report. */
export function main(): void {
  const items = [createItem("alpha", 3), createItem("beta", 5)];
  const first: Item = items[0];
  console.log(first.describe());

  save(processAll(items));
  console.log(Report(load()));
}

main();
