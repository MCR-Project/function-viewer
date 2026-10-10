#include <stdio.h>
#include "models.h"
#include "processing.h"
#include "storage.h"

static double square(double value) {
    return value * value;
}

/* Stand-in for reading a CSV; fills a fixed sample for the demo. */
static int load_readings(double *readings) {
    double sample[] = { 1.0, -2.0, 3.0, 4.0 };
    for (int i = 0; i < 4; i++) {
        readings[i] = sample[i];
    }
    return 4;
}

/* Entry point of the sample pipeline. */
int main(void) {
    double readings[8];
    int count = load_readings(readings);
    count = clean(readings, count);
    apply(readings, count, square);
    Report report = report_new("daily", aggregate(readings, count));
    return save_report(&report);
}
