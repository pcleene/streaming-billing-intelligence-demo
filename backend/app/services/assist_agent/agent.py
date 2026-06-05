"""Re-export shim.

The canonical `AssistAgent` lives in `graph.py` alongside the topology
it owns. This module is preserved as a stable import path so any
caller (or sister-agent worker) that imports
`app.services.assist_agent.agent.AssistAgent` keeps working.
"""

from .graph import AssistAgent, build_graph

__all__ = ["AssistAgent", "build_graph"]
