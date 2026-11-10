"""Public facade corresponding to the UML application service layer."""

from .catalog import CatalogMixin
from .core import Core
from .governance import GovernanceMixin
from .queries import QueryMixin
from .trading import TradingMixin


class Marketplace(CatalogMixin, TradingMixin, GovernanceMixin, QueryMixin, Core):
    """Compose independently readable use-case modules over one transaction port."""
