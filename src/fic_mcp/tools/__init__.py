
from ..registry import Registrar
from . import account, analytics, einvoice, entities, info, issued, others, products, received

MODULES = (account, entities, products, issued, einvoice, received, others, info, analytics)


def register_all(reg: Registrar) -> None:
    for module in MODULES:
        module.register(reg)
