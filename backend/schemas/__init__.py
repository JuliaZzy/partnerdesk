"""Pydantic objects the rest of the code passes around.

Tables stay in `db/orm.py`. `db/database.py` converts between the two. HTTP bodies for a
module live next to that module's objects.
"""

from __future__ import annotations

from .brand import (
    INCOTERMS_VALUES,
    PAYMENT_TERMS_LABELS,
    PAYMENT_TERMS_VALUES,
    SHIPPING_METHOD_VALUES,
    AgentSettings,
    Incoterms,
    PaymentTerms,
    ShippingMethod,
)
from .catalog import (
    PRODUCT_STATUS_ALIASES,
    PRODUCT_STATUS_VALUES,
    CommercialRule,
    Product,
    ProductStatus,
    product_status,
)
from .chat import Confirm, ConfirmContract, ConfirmReport, DataFeedback, Inbound, InboundFile, NewSession
from .contract import (
    Contract,
    ContractChunk,
    ContractDiscountRule,
    ContractDiscountTier,
    ContractEvidence,
    ContractMoq,
    ContractPriceEntry,
    ContractRecord,
    ContractTerms,
    ContractTerritory,
)
from .document import BrandDocument
from .memory import Memory, MemoryActive, MemoryIn
from .order import PurchaseOrder, PurchaseOrderItem
from .partnership import Partnership
from .po import Discount, EffectiveTerms, LineItemSlot, PoSlots
from .report import NarrativeReport, ReportFact, SalesReport

__all__ = [
    "INCOTERMS_VALUES",
    "PAYMENT_TERMS_LABELS",
    "PAYMENT_TERMS_VALUES",
    "PRODUCT_STATUS_ALIASES",
    "PRODUCT_STATUS_VALUES",
    "SHIPPING_METHOD_VALUES",
    "AgentSettings",
    "BrandDocument",
    "CommercialRule",
    "Confirm",
    "ConfirmContract",
    "ConfirmReport",
    "Contract",
    "ContractChunk",
    "ContractDiscountRule",
    "ContractDiscountTier",
    "ContractEvidence",
    "ContractMoq",
    "ContractPriceEntry",
    "ContractRecord",
    "ContractTerms",
    "ContractTerritory",
    "DataFeedback",
    "Discount",
    "EffectiveTerms",
    "Inbound",
    "InboundFile",
    "Incoterms",
    "LineItemSlot",
    "Memory",
    "MemoryActive",
    "MemoryIn",
    "NarrativeReport",
    "NewSession",
    "Partnership",
    "PaymentTerms",
    "PoSlots",
    "Product",
    "ProductStatus",
    "PurchaseOrder",
    "PurchaseOrderItem",
    "ReportFact",
    "SalesReport",
    "ShippingMethod",
    "product_status",
]
