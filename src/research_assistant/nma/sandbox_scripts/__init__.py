"""Sandbox-executed NMA scripts.

These modules are NOT imported into the host process — they run inside
the sandbox container where statsmodels (and, for the Bayesian backend,
PyMC) live. The host venv reads them as text via importlib.resources.
"""
