#pragma once

#include <cstddef>
#include <string>
#include <vector>

#include "models.h"

namespace inventory {

// Every item in stock, in the order it was added.
class Catalog {
public:
    // Adds `quantity` copies of an item.
    void add(const Item &item, int quantity = 1);

    // Adds an item made from its name and weight.
    void add(const std::string &name, double weight);

    // The first item with that name, or nullptr.
    const Item *find(const std::string &name) const;

    const std::vector<Item> &items() const { return items_; }
    std::size_t size() const { return items_.size(); }

private:
    std::vector<Item> items_;
};

}  // namespace inventory
