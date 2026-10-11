#ifndef PROCESSING_H
#define PROCESSING_H

/// Drops negative readings in place and returns how many are left.
int clean(double *readings, int count);

// Arithmetic mean of the readings; 0 when there are none.
double aggregate(const double *readings, int count);

/* Applies `transform` to every reading. Calls made through the pointer are not wired. */
void apply(double *readings, int count, double (*transform)(double));

#endif
