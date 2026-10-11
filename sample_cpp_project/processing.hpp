#pragma once

#include <vector>

#include "catalog.hpp"

namespace inventory::processing {

// The value, or `limit` when the value is above it.
template <typename T>
T cap(T value, T limit) {
    return limit < value ? limit : value;
}

// A running total of any numeric type; each step is capped at 1000.
template <typename T>
class Tally {
public:
    explicit Tally(T start) : total_(start) {}

    void add(T value) { total_ += cap(value, T(1000)); }
    T total() const;

private:
    T total_;
};

template <typename T>
T Tally<T>::total() const {
    return total_;
}

// Sum of all the weights in a catalog.
double total_weight(const Catalog &catalog);

// The items heavier than `limit`, in catalog order.
std::vector<Item> heavier_than(const Catalog &catalog, double limit);

}  // namespace inventory::processing
