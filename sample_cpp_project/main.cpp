#include <cstddef>
#include <iostream>

#include "catalog.hpp"
#include "exporter.hpp"
#include "processing.hpp"
#include "storage.hpp"

using namespace inventory;

// Sends every item through an exporter. `write` is virtual: the call is wired
// to Exporter::write, the declared class's own method, not to an override.
static void export_catalog(Exporter &exporter, const Catalog &catalog) {
    for (const Item &item : catalog.items()) {
        exporter.write(item);
    }
}

// Entry point of the sample: stock a catalog, weigh it, export it and save it.
int main() {
    Catalog catalog;
    catalog.add("alpha", 3.0);  // both overloads take two arguments: not wired
    catalog.add(Item("beta", 5.0));  // only add(const Item &, int) takes one
    catalog.add(Item("gamma", 0.5));

    auto heaviest = catalog.find("beta");  // auto: wired because one method is named describe
    if (heaviest != nullptr) {
        std::cout << heaviest->describe() << '\n';
    }

    std::size_t shown = processing::cap<std::size_t>(catalog.size(), 10);
    std::cout << "showing " << shown << " of " << catalog.size() << " items\n";

    processing::Tally heavy(0);  // Tally<int>, deduced from the argument
    for (const Item &item : processing::heavier_than(catalog, 1.0)) {
        heavy.add(1);
        std::cout << "heavy: " << item.describe() << '\n';
    }
    std::cout << heavy.total() << " heavy items, " << processing::total_weight(catalog) << " kg in all\n";

    Exporter plain(std::cout);
    export_catalog(plain, catalog);

    Store store("inventory.csv");
    store.save(catalog);
    return 0;
}
