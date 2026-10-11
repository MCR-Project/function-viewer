#include "exporter.hpp"

namespace inventory {

void Exporter::write(const Item &item) {
    out_ << item.describe() << '\n';
}

void Exporter::write_all(const std::vector<Item> &items) {
    for (const Item &item : items) {
        write(item);
    }
}

void CsvExporter::header() {
    out_ << "name,weight\n";
}

void CsvExporter::write(const Item &item) {
    out_ << item.name() << ',' << item.weight() << '\n';
}

}  // namespace inventory
