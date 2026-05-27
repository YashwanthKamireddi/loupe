"""Loupe — a magnifying glass for your AI agent.

Drop-in forensics + interpretability for LLM agents.

Quickstart
----------

    from loupe import trace, record_step
    from loupe.integrations import patch_all

    patch_all()                                # auto-capture every installed SDK

    @trace(framework="anthropic")
    async def my_agent(query: str):
        record_step("plan", "compose request")
        return await some_llm_call(query)

Traces land in ``~/.loupe/traces/``. View them with ``loupe ui``.

Public surface
--------------

Capture primitives:
    :func:`trace`, :func:`record_step`, :class:`Trace`, :class:`Step`,
    :func:`current_trace`, :class:`Store`.

Circuit attribution (v0.2):
    :class:`AttributionResult`, :class:`FeatureActivation`,
    :class:`Attributor` (Protocol), :class:`MockAttributor`,
    :class:`SAEAttributor`, :func:`make_attributor`, :func:`attribute_trace`.
"""

from loupe._version import __version__
from loupe.attribution import (
    AttributionResult,
    Attributor,
    FeatureActivation,
    MockAttributor,
    SAEAttributor,
    attribute_trace,
    make_attributor,
)
from loupe.attribution_patching import (
    AttributionPatcher,
    CausalFeature,
    PatchPair,
    PatchResult,
)
from loupe.steering import Steerer, SteerResult, SteerSpec
from loupe.store import Store
from loupe.trace import Step, Trace, current_trace, record_step, trace

__all__ = [
    "__version__",
    # Capture
    "Step",
    "Store",
    "Trace",
    "current_trace",
    "record_step",
    "trace",
    # Circuit attribution (correlational)
    "AttributionResult",
    "Attributor",
    "FeatureActivation",
    "MockAttributor",
    "SAEAttributor",
    "attribute_trace",
    "make_attributor",
    # Attribution patching (causal)
    "AttributionPatcher",
    "CausalFeature",
    "PatchPair",
    "PatchResult",
    # Steering (causal manipulation)
    "Steerer",
    "SteerResult",
    "SteerSpec",
]
