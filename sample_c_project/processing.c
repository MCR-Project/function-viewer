#include "processing.h"

// Readings above this are treated as sensor glitches.
static double clamp(double value) {
    return value > 1000.0 ? 1000.0 : value;
}

int clean(double *readings, int count) {
    int kept = 0;
    for (int i = 0; i < count; i++) {
        if (readings[i] >= 0.0) {
            readings[kept++] = clamp(readings[i]);
        }
    }
    return kept;
}

double aggregate(const double *readings, int count) {
    double total = 0.0;
    for (int i = 0; i < count; i++) {
        total += readings[i];
    }
    return count ? total / count : 0.0;
}

void apply(double *readings, int count, double (*transform)(double)) {
    for (int i = 0; i < count; i++) {
        readings[i] = transform(readings[i]);
    }
}
