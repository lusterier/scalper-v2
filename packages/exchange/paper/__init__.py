"""§12.1 PaperExchange — exchange simulator for paper-trading bots.

Public API:

* :class:`PaperExchange` — implements :class:`packages.exchange.ExchangeClient`.
* :data:`SlippageModel` — :class:`typing.Literal` for the per-bot slippage config.

T-211 ships the skeleton: 3-param constructor (``seed_balance``,
``slippage_model``, ``fee_rate``) + 10 method stubs raising
``NotImplementedError`` pointing at T-213. T-213 lands the fill-
semantics + paper_* persistence + execution-emission body.
"""

from __future__ import annotations

from .adapter import PaperExchange, SlippageModel

__all__ = ["PaperExchange", "SlippageModel"]
