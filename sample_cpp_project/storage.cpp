#include "storage.hpp"

#include <iostream>

#include "exporter.hpp"

namespace inventory {

Store::Store(const std::string &path) : file_(path) {
    log("opened");
}

Store::~Store() {
    file_.flush();
    log("closed");
}

void Store::save(const Catalog &catalog) {
    CsvExporter csv(file_);
    csv.header();
    csv.write_all(catalog.items());
    saved_ += static_cast<int>(catalog.size());
    log("saved");
}

void Store::log(const char *event) const {
    std::clog << "store " << event << " (" << saved_ << " items)\n";
}

}  // namespace inventory
