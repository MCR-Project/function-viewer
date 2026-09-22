// The untyped, CommonJS side of the project: no annotations anywhere.
const { Item } = require("./models");

const rows = [];

function put(row) {
  rows.push(row);
}

function serialize(result) {
  return { name: result.item.name, weight: result.item.weight, score: result.score };
}

function restore(row) {
  return { item: new Item(row.name, row.weight), score: row.score };
}

/** Stores every result as a plain row. */
function save(results) {
  results.forEach((result) => put(serialize(result)));
}

function load() {
  return rows.map((row) => restore(row));
}

module.exports = { save, load };
