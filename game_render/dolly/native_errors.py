"""Shared native-bridge errors, importable without the bridge itself."""

class NativeBridgeError(RuntimeError):
    """Native camera is unavailable or did not acknowledge a command."""
