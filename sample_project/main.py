"""Entry point of the sample pipeline."""

import processing
from storage import save_report
from models import Report


def main() -> None:
    """Run the full sample pipeline.

    Loads raw readings, cleans and aggregates them, then writes
    a report to disk.
    """
    readings = load_readings("data.csv")
    cleaned = processing.clean(readings)
    stats = processing.aggregate(cleaned)
    report = Report("daily", stats)
    save_report(report)
    print(report.summary())


def load_readings(path: str) -> list[float]:
    """Parse one float per line from *path*.

    Args:
        path: CSV file with one numeric reading per line.

    Returns:
        The readings, in file order.
    """
    with open(path) as fh:
        return [float(line) for line in fh if line.strip()]


if __name__ == "__main__":
    main()
