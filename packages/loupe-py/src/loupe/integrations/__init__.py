"""Framework-specific instrumentation for Loupe.

Each submodule registers hooks into one agent framework so existing user code
needs no manual `record_step` calls. The framework module is only imported
when the user opts in — keeping `loupe` core dependency-free.
"""
