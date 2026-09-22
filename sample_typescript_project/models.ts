export class Item {
  constructor(public name: string, public weight: number) {}

  /** Human-readable label for the item. */
  describe(): string {
    return `${this.name} (${this.weight})`;
  }

  heavier(other: Item): boolean {
    return this.weight > other.weight;
  }
}

export function createItem(name: string, weight: number): Item {
  return new Item(name, weight);
}
