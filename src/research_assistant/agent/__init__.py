"""Agent subpackage — deps, model, dispatcher, and per-workflow specialists.

Only `AgentDeps` is re-exported eagerly. The dispatcher and specialists
import `..tools` and would otherwise create a circular import when a tool
module loaded `..agent.deps` while this package was still initialising.
Import them directly:

    from .dispatcher import dispatch
    from .specialists import meta_analysis, general_qa
"""

from .deps import AgentDeps

__all__ = ["AgentDeps"]
