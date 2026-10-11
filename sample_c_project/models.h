#ifndef MODELS_H
#define MODELS_H

typedef struct {
    const char *name;
    double mean;
} Report;

/* Builds a report for a named series of readings. */
Report report_new(const char *name, double mean);

/**
 * Renders a report as a single line of text.
 * The result is written into `out`.
 */
void report_describe(const Report *report, char *out, int size);

#endif
