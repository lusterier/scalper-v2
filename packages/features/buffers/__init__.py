"""Public surface for :mod:`packages.features.buffers` (T-110a).

Re-exports the :class:`BufferRegistry` + :class:`BufferHandle` pair
used by ``feature-engine`` (T-110b) for refcounted rolling-candle
storage. See :mod:`packages.features.buffers.registry` for the H-014
contract and design rationale.
"""

from __future__ import annotations

from packages.features.buffers.registry import BufferHandle, BufferRegistry

__all__ = ["BufferHandle", "BufferRegistry"]
