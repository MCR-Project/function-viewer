"""Report model."""

from formatting import render_title, render_stats


class Report:
    """A named bundle of statistics ready for rendering."""

    def __init__(self, name: str, stats: dict):
        """Store the report *name* and its *stats* mapping."""
        self.name = name
        self.stats = stats

    def summary(self) -> str:
        """One-line human-readable summary of the report."""
        title = render_title(self.name)
        return f"{title}: {self.describe_stats()}"

    def describe_stats(self) -> str:
        """Render the stats mapping via the formatting helpers."""
        return render_stats(self.stats)
