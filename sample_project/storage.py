"""Persistence of rendered reports."""

from formatting import render_error


def save_report(report) -> None:
    """Write *report* to a text file named after it."""
    try:
        with open(f"{report.name}.txt", "w") as fh:
            fh.write(report.summary())
    except OSError as exc:
        print(render_error(str(exc), attempts=0))


def retry_save(message: str, attempts: int) -> str:
    """Pretend to retry a failed save (cycle: storage <-> formatting)."""
    return render_error(f"retrying after: {message}", attempts)
