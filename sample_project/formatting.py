"""String rendering helpers (contains a deliberate call cycle with storage)."""


def render_title(name: str) -> str:
    """Uppercase, decorated report title."""
    return f"=== {name.upper()} ==="


def render_stats(stats: dict) -> str:
    """Render a stats mapping as `key=value` pairs."""
    return ", ".join(f"{key}={format_number(value)}" for key, value in stats.items())


def format_number(value) -> str:
    """Format numbers compactly; falls back to str() for the rest."""
    if isinstance(value, float):
        return f"{value:.2f}"
    return str(value)


def render_error(message: str, attempts: int) -> str:
    """Format an error, retrying the failed save once (cycle: formatting <-> storage)."""
    import storage

    if attempts < 1:
        return storage.retry_save(message, attempts + 1)
    return f"ERROR: {message}"
