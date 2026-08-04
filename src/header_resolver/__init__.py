"""header_resolver — tiered schema matching for partner CSV uploads.

    from header_resolver import Resolver, Schema

    schema = Schema.load_builtin("policy_v2")
    result = Resolver(schema).resolve(["Policy Numbr", "Nominee DOB", "col_5"])

    for m in result.mappings:
        print(m.source, "->", m.target, m.confidence, m.resolved_by.value)
"""

from .aliases import AliasKey, AliasStore
from .guards import GuardList, build_guard_list
from .layer4 import Layer4Config, Layer4Resolver
from .llm import AnthropicClient, LLMError, MockLLMClient
from .models import CanonicalField, Layer, Mapping, ResolveResult, ReviewReason
from .normalize import normalize
from .resolver import Resolver, Thresholds
from .schema import EntityGroup, Schema

__version__ = "0.2.0"

__all__ = [
    "AliasKey",
    "AliasStore",
    "AnthropicClient",
    "CanonicalField",
    "EntityGroup",
    "GuardList",
    "Layer",
    "Layer4Config",
    "Layer4Resolver",
    "LLMError",
    "Mapping",
    "MockLLMClient",
    "ResolveResult",
    "Resolver",
    "ReviewReason",
    "Schema",
    "Thresholds",
    "build_guard_list",
    "normalize",
]
