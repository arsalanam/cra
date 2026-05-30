"""Sandbox-executed scripts for the trial_stats specialist.

These modules are NOT imported into the host process — they run inside
the sandbox container where pandas/numpy/scipy/statsmodels/matplotlib
are pinned. The host venv only reads them as text via importlib.resources
and forwards the contents to `sandbox_exec`.
"""
