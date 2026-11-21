"""Shared actionable errors for launch and configuration recovery."""

class LaunchError(RuntimeError):
    """Actionable launch failure; suitable for display in the GUI."""
