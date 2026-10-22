#ifndef INVENTORY_MODELS_H
#define INVENTORY_MODELS_H

#include <string>

namespace inventory {

// A named thing kept in stock.
class Item {
public:
    /* Builds an item; the weight is in kilograms. */
    Item(std::string name, double weight);

    const std::string &name() const { return name_; }
    double weight() const { return weight_; }

    /// Human-readable label, e.g. "alpha (3 kg)".
    std::string describe() const;

    // Items are ordered by weight, lightest first.
    bool operator<(const Item &other) const;

private:
    std::string name_;
    double weight_;
};

}  // namespace inventory

#endif
