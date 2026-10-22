#include "processing.hpp"

namespace inventory::processing {

namespace {

bool is_heavy(const Item &item, double limit) {
    return item.weight() > limit;
}

}  // namespace

double total_weight(const Catalog &catalog) {
    Tally<double> tally(0.0);
    for (const Item &item : catalog.items()) {
        tally.add(item.weight());
    }
    return tally.total();
}

std::vector<Item> heavier_than(const Catalog &catalog, double limit) {
    std::vector<Item> found;
    auto keep = [&](const Item &item) {
        if (is_heavy(item, limit)) {
            found.push_back(item);
        }
    };
    for (const Item &item : catalog.items()) {
        keep(item);
    }
    return found;
}

}  // namespace inventory::processing
