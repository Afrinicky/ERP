"""Request and response contracts for the catalogue, parties and import.

Monetary values cross the boundary as strings, so no amount passes through a JSON
float, per PRD 9.5.
"""

from __future__ import annotations

import uuid
from datetime import date
from decimal import Decimal
from typing import Any

from pydantic import Field, field_validator

from nickland.interface.schemas import Contract, Reply

# --------------------------------------------------------------------------------------
# Units
# --------------------------------------------------------------------------------------


class UnitReply(Reply):
    id: uuid.UUID
    code: str
    name: str
    plural_name: str | None
    kind: str
    base_fk: uuid.UUID | None
    factor: Decimal
    decimal_places: int
    is_active: bool
    is_system: bool


class CreateUnitContract(Contract):
    code: str = Field(min_length=2, max_length=16)
    name: str = Field(min_length=1, max_length=60)
    kind: str = Field(default="count", max_length=12)
    base_code: str | None = Field(default=None, max_length=16)
    factor: Decimal = Field(default=Decimal("1"), gt=0)
    plural_name: str | None = Field(default=None, max_length=60)
    decimal_places: int = Field(default=0, ge=0, le=6)


class ConvertContract(Contract):
    amount: Decimal
    from_unit_id: uuid.UUID
    to_unit_id: uuid.UUID
    product_id: uuid.UUID | None = None


# --------------------------------------------------------------------------------------
# Categories and attributes
# --------------------------------------------------------------------------------------


class CategoryReply(Reply):
    id: uuid.UUID
    code: str
    name: str
    description: str | None
    parent_fk: uuid.UUID | None
    path: str
    depth: int
    display_order: int
    is_active: bool
    default_tax_code: str | None


class CreateCategoryContract(Contract):
    code: str = Field(min_length=3, max_length=40)
    name: str = Field(min_length=1, max_length=120)
    parent_id: uuid.UUID | None = None
    description: str | None = Field(default=None, max_length=2000)
    default_tax_code: str | None = Field(default=None, max_length=40)
    display_order: int = Field(default=0, ge=0, le=9999)


class AttributeReply(Reply):
    id: uuid.UUID
    code: str
    name: str
    kind: str
    choices: list[str] | None
    unit_label: str | None
    is_variant_defining: bool
    display_order: int
    is_active: bool


class CreateAttributeContract(Contract):
    code: str = Field(min_length=3, max_length=40)
    name: str = Field(min_length=1, max_length=80)
    kind: str = Field(default="text", max_length=12)
    choices: list[str] | None = Field(default=None, max_length=200)
    is_variant_defining: bool = False
    unit_label: str | None = Field(default=None, max_length=16)
    display_order: int = Field(default=0, ge=0, le=9999)


# --------------------------------------------------------------------------------------
# Products
# --------------------------------------------------------------------------------------


class BarcodeReply(Reply):
    id: uuid.UUID
    value: str
    kind: str
    unit_fk: uuid.UUID | None
    is_primary: bool


class AttributeValueReply(Reply):
    attribute_fk: uuid.UUID
    value: str


class ProductReply(Reply):
    id: uuid.UUID
    sku: str
    name: str
    description: str | None
    item_class: str
    category_fk: uuid.UUID | None
    parent_fk: uuid.UUID | None
    is_variant_parent: bool
    variant_label: str | None
    variant_values: dict[str, str] | None
    stock_unit_fk: uuid.UUID
    sales_unit_fk: uuid.UUID | None
    purchase_unit_fk: uuid.UUID | None
    tracking_policy: str
    requires_expiry: bool
    shelf_life_days: int | None
    valuation_policy: str
    manufacturer: str | None
    model_code: str | None
    country_of_origin: str | None
    list_price: Decimal | None
    currency_code: str | None
    tax_code: str | None
    weight_kg: Decimal | None
    volume_m3: Decimal | None
    reorder_level: Decimal | None
    reorder_quantity: Decimal | None
    lead_time_days: int | None
    is_active: bool
    is_sellable: bool
    is_purchasable: bool
    barcodes: list[BarcodeReply] = Field(default_factory=list)
    attribute_values: list[AttributeValueReply] = Field(default_factory=list)
    holds_stock: bool = False
    """Derived from the classification, so a caller need not infer it, per PRD 6.1."""


class ProductPageReply(Reply):
    items: list[ProductReply]
    total: int
    offset: int
    limit: int


class BarcodeContract(Contract):
    value: str = Field(min_length=1, max_length=48)
    kind: str = Field(default="internal", max_length=12)
    unit_id: uuid.UUID | None = None
    is_primary: bool = False


class ComponentContract(Contract):
    product_id: uuid.UUID
    quantity: Decimal = Field(gt=0)
    unit_id: uuid.UUID


class CreateProductContract(Contract):
    sku: str = Field(min_length=3, max_length=40)
    name: str = Field(min_length=1, max_length=200)
    stock_unit_id: uuid.UUID
    item_class: str = Field(default="stocked", max_length=16)
    description: str | None = Field(default=None, max_length=4000)
    category_id: uuid.UUID | None = None
    sales_unit_id: uuid.UUID | None = None
    purchase_unit_id: uuid.UUID | None = None
    tracking_policy: str = Field(default="none", max_length=12)
    requires_expiry: bool = False
    shelf_life_days: int | None = Field(default=None, gt=0, le=36500)
    valuation_policy: str = Field(default="business_default", max_length=20)
    manufacturer: str | None = Field(default=None, max_length=160)
    model_code: str | None = Field(default=None, max_length=80)
    country_of_origin: str | None = Field(default=None, max_length=80)
    list_price: Decimal | None = Field(default=None, ge=0)
    tax_code: str | None = Field(default=None, max_length=40)
    weight_kg: Decimal | None = Field(default=None, ge=0)
    volume_m3: Decimal | None = Field(default=None, ge=0)
    reorder_level: Decimal | None = Field(default=None, ge=0)
    reorder_quantity: Decimal | None = Field(default=None, ge=0)
    lead_time_days: int | None = Field(default=None, ge=0, le=3650)
    is_sellable: bool = True
    is_purchasable: bool = True
    attributes: dict[str, str] = Field(default_factory=dict)
    barcodes: list[BarcodeContract] = Field(default_factory=list, max_length=20)
    components: list[ComponentContract] = Field(default_factory=list, max_length=100)


class AmendProductContract(Contract):
    sku: str | None = Field(default=None, min_length=3, max_length=40)
    name: str | None = Field(default=None, min_length=1, max_length=200)
    description: str | None = Field(default=None, max_length=4000)
    item_class: str | None = Field(default=None, max_length=16)
    category_id: uuid.UUID | None = None
    sales_unit_id: uuid.UUID | None = None
    purchase_unit_id: uuid.UUID | None = None
    tracking_policy: str | None = Field(default=None, max_length=12)
    requires_expiry: bool | None = None
    shelf_life_days: int | None = Field(default=None, gt=0, le=36500)
    valuation_policy: str | None = Field(default=None, max_length=20)
    manufacturer: str | None = Field(default=None, max_length=160)
    model_code: str | None = Field(default=None, max_length=80)
    country_of_origin: str | None = Field(default=None, max_length=80)
    list_price: Decimal | None = Field(default=None, ge=0)
    tax_code: str | None = Field(default=None, max_length=40)
    weight_kg: Decimal | None = Field(default=None, ge=0)
    volume_m3: Decimal | None = Field(default=None, ge=0)
    reorder_level: Decimal | None = Field(default=None, ge=0)
    reorder_quantity: Decimal | None = Field(default=None, ge=0)
    lead_time_days: int | None = Field(default=None, ge=0, le=3650)
    is_sellable: bool | None = None
    is_purchasable: bool | None = None
    is_active: bool | None = None


class SetAttributesContract(Contract):
    values: dict[str, str] = Field(max_length=60)


class GenerateVariantsContract(Contract):
    combinations: list[dict[str, str]] = Field(min_length=1, max_length=200)


class ProductFactorContract(Contract):
    unit_id: uuid.UUID
    factor: Decimal = Field(gt=0)


# --------------------------------------------------------------------------------------
# Price lists
# --------------------------------------------------------------------------------------


class PriceListReply(Reply):
    id: uuid.UUID
    code: str
    name: str
    currency_code: str
    is_default: bool
    is_active: bool
    tax_inclusive: bool
    effective_from: date | None
    effective_to: date | None


class CreatePriceListContract(Contract):
    code: str = Field(min_length=3, max_length=40)
    name: str = Field(min_length=1, max_length=120)
    currency_code: str = Field(min_length=3, max_length=3)
    is_default: bool = False
    tax_inclusive: bool = False
    effective_from: date | None = None
    effective_to: date | None = None


class PriceListLineReply(Reply):
    id: uuid.UUID
    price_list_fk: uuid.UUID
    product_fk: uuid.UUID
    unit_fk: uuid.UUID | None
    minimum_quantity: Decimal
    price: Decimal


class SetPriceContract(Contract):
    product_id: uuid.UUID
    price: Decimal = Field(ge=0)
    minimum_quantity: Decimal = Field(default=Decimal("0"), ge=0)
    unit_id: uuid.UUID | None = None


class ResolvedPriceReply(Reply):
    price: Decimal
    source: str
    source_label: str
    currency_code: str | None
    tax_inclusive: bool
    minimum_quantity: Decimal


# --------------------------------------------------------------------------------------
# Parties
# --------------------------------------------------------------------------------------


class ContactReply(Reply):
    id: uuid.UUID
    name: str
    position: str | None
    email: str | None
    telephone: str | None
    is_primary: bool
    notes: str | None


class PartyReply(Reply):
    id: uuid.UUID
    code: str
    name: str
    legal_name: str | None
    party_type: str
    is_customer: bool
    is_supplier: bool
    is_affiliate: bool
    email: str | None
    telephone: str | None
    website: str | None
    tax_identifier: str | None
    registration_number: str | None
    tax_treatment: str
    tax_exemption_reference: str | None
    tax_exemption_expires: date | None
    payment_term_kind: str
    payment_term_days: int
    payment_terms_label: str = ""
    credit_limit: Decimal | None
    credit_on_hold: bool
    credit_hold_reason: str | None
    price_list_fk: uuid.UUID | None
    discount_percent: Decimal | None
    default_lead_time_days: int | None
    bank_name: str | None
    bank_account_name: str | None
    bank_account_number: str | None
    mobile_money_provider: str | None
    mobile_money_number: str | None
    notes: str | None
    is_active: bool
    contacts: list[ContactReply] = Field(default_factory=list)


class PartyPageReply(Reply):
    items: list[PartyReply]
    total: int
    offset: int
    limit: int


class ContactContract(Contract):
    name: str = Field(min_length=1, max_length=160)
    position: str | None = Field(default=None, max_length=120)
    email: str | None = Field(default=None, max_length=200)
    telephone: str | None = Field(default=None, max_length=60)
    is_primary: bool = False
    notes: str | None = Field(default=None, max_length=2000)


class CreatePartyContract(Contract):
    name: str = Field(min_length=1, max_length=200)
    code: str | None = Field(default=None, min_length=3, max_length=24)
    legal_name: str | None = Field(default=None, max_length=200)
    party_type: str = Field(default="organisation", max_length=16)
    is_customer: bool = False
    is_supplier: bool = False
    is_affiliate: bool = False
    email: str | None = Field(default=None, max_length=200)
    telephone: str | None = Field(default=None, max_length=60)
    website: str | None = Field(default=None, max_length=200)
    tax_identifier: str | None = Field(default=None, max_length=60)
    registration_number: str | None = Field(default=None, max_length=60)
    tax_treatment: str = Field(default="standard", max_length=24)
    tax_exemption_reference: str | None = Field(default=None, max_length=120)
    tax_exemption_expires: date | None = None
    payment_term_kind: str = Field(default="immediate", max_length=16)
    payment_term_days: int = Field(default=0, ge=0, le=365)
    credit_limit: Decimal | None = Field(default=None, ge=0)
    price_list_id: uuid.UUID | None = None
    discount_percent: Decimal | None = Field(default=None, ge=0, lt=1)
    default_lead_time_days: int | None = Field(default=None, ge=0, le=3650)
    bank_name: str | None = Field(default=None, max_length=120)
    bank_account_name: str | None = Field(default=None, max_length=160)
    bank_account_number: str | None = Field(default=None, max_length=60)
    mobile_money_provider: str | None = Field(default=None, max_length=60)
    mobile_money_number: str | None = Field(default=None, max_length=60)
    notes: str | None = Field(default=None, max_length=4000)
    contacts: list[ContactContract] = Field(default_factory=list, max_length=40)


class AmendPartyContract(Contract):
    name: str | None = Field(default=None, min_length=1, max_length=200)
    legal_name: str | None = Field(default=None, max_length=200)
    party_type: str | None = Field(default=None, max_length=16)
    is_customer: bool | None = None
    is_supplier: bool | None = None
    is_affiliate: bool | None = None
    email: str | None = Field(default=None, max_length=200)
    telephone: str | None = Field(default=None, max_length=60)
    website: str | None = Field(default=None, max_length=200)
    tax_identifier: str | None = Field(default=None, max_length=60)
    registration_number: str | None = Field(default=None, max_length=60)
    tax_treatment: str | None = Field(default=None, max_length=24)
    tax_exemption_reference: str | None = Field(default=None, max_length=120)
    tax_exemption_expires: date | None = None
    payment_term_kind: str | None = Field(default=None, max_length=16)
    payment_term_days: int | None = Field(default=None, ge=0, le=365)
    credit_limit: Decimal | None = Field(default=None, ge=0)
    price_list_id: uuid.UUID | None = None
    discount_percent: Decimal | None = Field(default=None, ge=0, lt=1)
    default_lead_time_days: int | None = Field(default=None, ge=0, le=3650)
    bank_name: str | None = Field(default=None, max_length=120)
    bank_account_name: str | None = Field(default=None, max_length=160)
    bank_account_number: str | None = Field(default=None, max_length=60)
    mobile_money_provider: str | None = Field(default=None, max_length=60)
    mobile_money_number: str | None = Field(default=None, max_length=60)
    notes: str | None = Field(default=None, max_length=4000)
    is_active: bool | None = None


class CreditHoldContract(Contract):
    on_hold: bool
    reason: str = Field(min_length=1, max_length=500)


class CreditReply(Reply):
    standing: str
    limit: Decimal
    outstanding: Decimal
    overdue: Decimal
    available: Decimal
    detail: str
    permits_credit_sale: bool


class SupplierProductReply(Reply):
    id: uuid.UUID
    supplier_fk: uuid.UUID
    product_fk: uuid.UUID
    supplier_sku: str | None
    supplier_name: str | None
    unit_fk: uuid.UUID | None
    units_per_purchase_unit: Decimal
    last_price: Decimal | None
    currency_code: str | None
    price_recorded_on: date | None
    lead_time_days: int | None
    minimum_order_quantity: Decimal | None
    is_preferred: bool
    is_active: bool
    notes: str | None


class LinkSupplierProductContract(Contract):
    supplier_id: uuid.UUID
    product_id: uuid.UUID
    supplier_sku: str | None = Field(default=None, max_length=60)
    supplier_name: str | None = Field(default=None, max_length=200)
    unit_id: uuid.UUID | None = None
    units_per_purchase_unit: Decimal = Field(default=Decimal("1"), gt=0)
    last_price: Decimal | None = Field(default=None, ge=0)
    currency_code: str | None = Field(default=None, min_length=3, max_length=3)
    lead_time_days: int | None = Field(default=None, ge=0, le=3650)
    minimum_order_quantity: Decimal | None = Field(default=None, ge=0)
    is_preferred: bool = False
    notes: str | None = Field(default=None, max_length=2000)


class CustomerPriceContract(Contract):
    customer_id: uuid.UUID
    product_id: uuid.UUID
    price: Decimal = Field(ge=0)
    unit_id: uuid.UUID | None = None
    minimum_quantity: Decimal = Field(default=Decimal("0"), ge=0)
    currency_code: str | None = Field(default=None, min_length=3, max_length=3)
    effective_from: date | None = None
    effective_to: date | None = None
    agreement_reference: str | None = Field(default=None, max_length=120)


class AffiliateProfileReply(Reply):
    id: uuid.UUID
    party_fk: uuid.UUID
    scope: str
    method: str
    rate: Decimal
    fixed_amount: Decimal | None
    currency_code: str | None
    effective_from: date | None
    effective_to: date | None
    payout_trigger: str
    agreement_reference: str | None
    notes: str | None
    is_active: bool
    eligible_product_ids: list[uuid.UUID] = Field(default_factory=list)
    eligible_category_ids: list[uuid.UUID] = Field(default_factory=list)


class SetAffiliateProfileContract(Contract):
    party_id: uuid.UUID
    scope: str = Field(max_length=24)
    method: str = Field(default="percentage", max_length=12)
    rate: Decimal = Field(default=Decimal("0"), ge=0, lt=1)
    fixed_amount: Decimal | None = Field(default=None, ge=0)
    currency_code: str | None = Field(default=None, min_length=3, max_length=3)
    effective_from: date | None = None
    effective_to: date | None = None
    payout_trigger: str = Field(default="customer_payment", max_length=24)
    agreement_reference: str | None = Field(default=None, max_length=120)
    eligible_product_ids: list[uuid.UUID] = Field(default_factory=list, max_length=500)
    eligible_category_ids: list[uuid.UUID] = Field(default_factory=list, max_length=200)
    notes: str | None = Field(default=None, max_length=2000)


# --------------------------------------------------------------------------------------
# Import
# --------------------------------------------------------------------------------------


class RowReportReply(Reply):
    line: int
    outcome: str
    reference: str
    message: str
    field: str | None


class ImportReportReply(Reply):
    kind: str
    filename: str
    total: int
    to_create: int
    to_amend: int
    rejected: int
    acceptable: bool
    committed: bool
    summary: str
    run_id: uuid.UUID | None
    rows: list[RowReportReply] = Field(default_factory=list)


class ImportRunReply(Reply):
    id: uuid.UUID
    kind: str
    filename: str
    status: str
    started_at: Any
    completed_at: Any
    total_rows: int
    accepted_rows: int
    rejected_rows: int
    created_rows: int
    amended_rows: int
    failure_reason: str | None


class ImportContract(Contract):
    """An import submitted as base64, so the contract stays JSON throughout."""

    kind: str = Field(max_length=16)
    filename: str = Field(min_length=1, max_length=255)
    content_base64: str = Field(min_length=1, max_length=24_000_000)

    @field_validator("content_base64")
    @classmethod
    def _decodable(cls, value: str) -> str:
        import base64
        import binascii

        try:
            base64.b64decode(value, validate=True)
        except (binascii.Error, ValueError) as error:
            raise ValueError("The file content is not valid base64.") from error
        return value

    def content(self) -> bytes:
        import base64

        return base64.b64decode(self.content_base64, validate=True)
