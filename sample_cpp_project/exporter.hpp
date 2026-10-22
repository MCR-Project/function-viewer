#pragma once

#include <ostream>
#include <vector>

#include "models.h"

namespace inventory {

// Writes items to a stream, one plain line each.
class Exporter {
public:
    explicit Exporter(std::ostream &out) : out_(out) {}
    virtual ~Exporter() = default;

    virtual void write(const Item &item);
    void write_all(const std::vector<Item> &items);

protected:
    std::ostream &out_;
};

// Writes items as comma-separated values.
class CsvExporter : public Exporter {
public:
    explicit CsvExporter(std::ostream &out) : Exporter(out) {}

    void header();
    void write(const Item &item) override;
};

}  // namespace inventory
