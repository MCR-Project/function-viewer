#include "models.h"

#include <sstream>
#include <utility>

namespace inventory {

Item::Item(std::string name, double weight) : name_(std::move(name)), weight_(weight) {}

std::string Item::describe() const {
    std::ostringstream out;
    out << name() << " (" << weight() << " kg)";
    return out.str();
}

bool Item::operator<(const Item &other) const {
    return weight() < other.weight();
}

}  // namespace inventory
