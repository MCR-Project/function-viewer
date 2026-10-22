#pragma once

#include <fstream>
#include <string>

#include "catalog.hpp"

namespace inventory {

// Saves catalogs to a CSV file, which stays open as long as the store does.
class Store {
public:
    explicit Store(const std::string &path);
    ~Store();

    void save(const Catalog &catalog);

private:
    void log(const char *event) const;

    std::ofstream file_;
    int saved_ = 0;
};

}  // namespace inventory
