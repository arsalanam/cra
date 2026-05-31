"""Sandbox-executed IPD MA scripts.

NOT imported into the host process — they run inside the sandbox where
statsmodels lives. The host venv reads them as text via
importlib.resources.
"""
