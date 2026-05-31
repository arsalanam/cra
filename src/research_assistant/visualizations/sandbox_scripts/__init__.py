"""Sandbox-executed visualisation scripts.

These modules are NOT imported into the host process — they run inside
the sandbox container. The host venv only reads them as text via
importlib.resources and forwards the contents to `sandbox_exec`.
"""
