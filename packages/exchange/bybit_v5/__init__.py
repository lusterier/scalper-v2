"""§11.2 Bybit V5 REST client + signing + retry matrix (T-207).

T-208 will add ``BybitV5Adapter`` (ExchangeClient Protocol) on top.
"""

from .client import BybitV5Client

__all__ = ["BybitV5Client"]
