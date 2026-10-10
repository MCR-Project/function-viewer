#include <stdio.h>
#include "models.h"

Report report_new(const char *name, double mean) {
    Report report = { name, mean };
    return report;
}

void report_describe(const Report *report, char *out, int size) {
    snprintf(out, size, "%s: mean=%.2f", report->name, report->mean);
}
