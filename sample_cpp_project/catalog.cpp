#include "catalog.hpp"

namespace inventory {

void Catalog::add(const Item &item, int quantity) {
    for (int i = 0; i < quantity; ++i) {
        items_.push_back(item);
    }
}

void Catalog::add(const std::string &name, double weight) {
    add(Item(name, weight));
}

const Item *Catalog::find(const std::string &name) const {
    for (const Item &item : items_) {
        if (item.name() == name) {
            return &item;
        }
    }
    return nullptr;
}

}  // namespace inventory
