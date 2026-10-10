#include <stdio.h>
#include "storage.h"

static const char *output_path(void) {
    return "report.txt";
}

int save_report(const Report *report) {
    char line[128];
    FILE *file = fopen(output_path(), "w");
    if (file == NULL) {
        return -1;
    }
    report_describe(report, line, sizeof line);
    fprintf(file, "%s\n", line);
    fclose(file);
    return 0;
}
