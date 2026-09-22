import { Row } from "./formatting";
import { Item } from "./models";

/**
 * A function component renders another named component (a Wire to Row), a
 * lowercase host element and a fragment (neither is a Wire), and a class
 * tag (Item has no single id in this plugin, so it stays unresolved too).
 */
export function List({ items }: { items: Item[] }) {
  return (
    <>
      <div className="list">
        {items.map((item) => (
          <Row key={item.name} item={item} />
        ))}
      </div>
      <Item />
    </>
  );
}
