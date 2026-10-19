"""Schema the text-to-SQL model is allowed to see.

The catalog is one line per table (table choice). Column detail is sent only for the
tables that question needs, plus the join parents those tables cannot be read without.

`sessions` is omitted: it holds confirm tokens, draft slots and the chat transcript.
`text2sql_logs` is omitted so a query cannot read or rewrite its own audit.
`brand_documents.file_bytes` is omitted; page text lives on `brand_document_pages`.

Every view already filters to the current partnership, or to its brand for catalog and
brand material. The model must not add its own partnership predicate.
"""

from __future__ import annotations

from typing import Any

# Tables that exist in the database and must never be queried from here.
EXCLUDED_TABLES = frozenset({"sessions", "text2sql_logs"})
# Columns that exist on a queryable table but are not in its view.
HIDDEN_COLUMNS = {"brand_documents": frozenset({"file_bytes"})}

MAX_TABLES = 8


def _col(type_: str, description: str, examples: list[Any] | None = None) -> dict[str, Any]:
    column = {"column_type": type_, "column_description": description}
    if examples is not None:
        column["examples"] = examples
    return column


def _table(
    description: str,
    columns: dict[str, dict[str, Any]],
    *,
    where: str,
    joins: list[str] | None = None,
    source: str | None = None,
    alias: str | None = None,
) -> dict[str, Any]:
    return {
        "description": description,
        "columns": columns,
        "where": where,
        "joins": joins or [],
        "from": source,
        "alias": alias,
    }


_PARTNER = "partnership_id = :partnership_id"
_BRAND = "brand_id = :brand_id"

# Join a child table back to the partnership through a parent that carries the scope key.
_PO_CHILD = "public.{table} AS t JOIN public.purchase_orders AS po ON po.id = t.po_id"
_CONTRACT_CHILD = "public.{table} AS t JOIN public.contracts AS c ON c.id = t.contract_id"
_REPORT_CHILD = "public.{table} AS t JOIN public.sales_reports AS r ON r.id = t.report_id"


TABLES: dict[str, dict[str, Any]] = {
    "brands": _table(
        "The brand this distributor works with. One row.",
        {
            "id": _col("text", "Primary key."),
            "name": _col("text", "Brand name.", ["Northwind Foods"]),
            "preferred_currency": _col("text", "ISO currency the brand prices in.", ["USD", "EUR", "CNY"]),
            "created_at": _col("timestamptz", "When the row was created, UTC."),
            "updated_at": _col("timestamptz", "When the row was last updated, UTC."),
        },
        where="id = :brand_id",
    ),
    "distributors": _table(
        "This distributor. One row.",
        {
            "id": _col("text", "Primary key."),
            "name": _col("text", "Distributor legal name."),
            "country": _col("text", "ISO 3166-1 alpha-2 country, or null.", ["US", "DE", "CN"]),
            "created_at": _col("timestamptz", "When the row was created, UTC."),
            "updated_at": _col("timestamptz", "When the row was last updated, UTC."),
        },
        where="id = :distributor_id",
    ),
    "partnerships": _table(
        "The brand–distributor relationship this chat belongs to. One row. Currency and ship-to live here.",
        {
            "id": _col("text", "Primary key. The current partnership."),
            "brand_id": _col("text", "brands.id."),
            "distributor_id": _col("text", "distributors.id."),
            "ship_to_country": _col("text", "Default ship-to country, ISO alpha-2.", ["US"]),
            "currency": _col("text", "Currency of this relationship.", ["USD"]),
            "created_at": _col("timestamptz", "When the relationship was created, UTC."),
            "updated_at": _col("timestamptz", "When the relationship was last updated, UTC."),
        },
        where="id = :partnership_id",
        joins=["brands", "distributors"],
    ),
    "products": _table(
        "This brand's product catalog. Large (hundreds of thousands of rows). Filter by sku or name, or aggregate. "
        "Do not list the catalog. case_price is per case; moq_units is units, not cases.",
        {
            "id": _col("text", "Primary key."),
            "brand_id": _col("text", "brands.id."),
            "sku": _col("text", "Stock keeping unit, unique per brand.", ["FO-100"]),
            "product_name_en": _col("text", "English product name."),
            "product_name_native": _col("text", "Name in the label's own language. Equals the English name on English labels."),
            "category": _col("text", "Category."),
            "subcategory": _col("text", "Subcategory."),
            "variant": _col("text", "Variant, such as flavour or shade."),
            "form": _col("text", "Physical form, such as capsule or bottle."),
            "unit_size": _col("text", "Selling unit size as labeled.", ["100 capsules"]),
            "net_content": _col("text", "Net content as labeled.", ["500 ml"]),
            "description_en": _col("text", "English description. Long; do not select it unless asked."),
            "case_pack": _col("integer", "Units per case."),
            "case_price": _col("numeric", "Price of one case, in the brand currency. Null when unpriced."),
            "moq_units": _col("integer", "Minimum order quantity in units, not cases."),
            "cases_per_layer": _col("integer", "Cases in one pallet layer."),
            "layers_per_pallet": _col("integer", "Layers on one pallet."),
            "cases_per_pallet": _col("integer", "Cases on one full pallet."),
            "case_weight": _col("numeric", "Weight of one case."),
            "product_status": _col("text", "Commercial status.", ["正品", "赠品", "样品", "停产"]),
            "policy_tags": _col("jsonb", "List of policy tag strings."),
            "restricted_territories": _col("jsonb", "List of ISO country codes the product cannot ship to. Often empty."),
            "created_at": _col("timestamptz", "When the row was created, UTC."),
            "updated_at": _col("timestamptz", "When the row was last updated, UTC."),
        },
        where=_BRAND,
    ),
    "commercial_rules": _table(
        "Brand commercial rules that gate a purchase order. rule_config's shape depends on rule_type. "
        "A null contract_id means the rule was not derived from an agreement.",
        {
            "id": _col("text", "Primary key."),
            "brand_id": _col("text", "brands.id."),
            "contract_id": _col("text", "contracts.id when the rule was derived from an agreement, else null."),
            "name": _col("text", "Short name shown to people."),
            "rule_type": _col(
                "text", "Which check this is.",
                ["min_order_value", "gift_pct_cap", "territory_restriction", "discount_approval", "unit_cap", "value_pct_cap"],
            ),
            "rule_config": _col("jsonb", "Parameters for rule_type. Do not invent keys; select the object."),
            "severity": _col("text", "block stops the order; advisory does not.", ["block", "advisory"]),
            "is_active": _col("boolean", "Inactive rules are not applied."),
            "partner_scope": _col("text", "all, or a listed set of partners.", ["all"]),
            "partner_ids": _col("jsonb", "Partnership or distributor ids when partner_scope is not all."),
            "partner_regions": _col("jsonb", "Region codes the rule applies to."),
            "product_scope": _col("text", "all, or a listed set of products.", ["all"]),
            "product_ids": _col("jsonb", "products.id values when product_scope is not all."),
            "product_roles": _col("jsonb", "Product roles the rule applies to."),
            "product_tags": _col("jsonb", "policy_tags the rule applies to."),
            "effective_from": _col("text", "First calendar day YYYY-MM-DD, or null. Not a timestamp."),
            "effective_until": _col("text", "Last calendar day YYYY-MM-DD, or null. Not a timestamp."),
            "created_at": _col("timestamptz", "When the row was created, UTC."),
            "updated_at": _col("timestamptz", "When the row was last updated, UTC."),
        },
        where=_BRAND,
        joins=["contracts"],
    ),
    "brand_agent_settings": _table(
        "Default commercial terms and standing instructions for the brand's desk.",
        {
            "brand_id": _col("text", "Primary key. brands.id."),
            "payment_terms": _col("text", "Default payment terms.", ["50_50", "100_prepaid", "net_30", "net_60"]),
            "incoterms": _col("text", "Default incoterm.", ["FOB", "CIF", "EXW", "DDP", "DAP", "CFR"]),
            "shipping_method": _col("text", "Default shipping method.", ["sea", "air", "express", "land"]),
            "eta_days": _col("integer", "Default transit time in days."),
            "operating_context": _col("text", "Standing instructions. Long; select only when asked what the brand requires."),
            "min_po_confidence": _col("numeric", "Minimum extraction confidence before a draft can be confirmed."),
            "tone": _col("text", "How the desk should sound."),
            "updated_at": _col("timestamptz", "When the settings were last updated, UTC."),
        },
        where=_BRAND,
    ),
    "contracts": _table(
        "Agreements filed for this partnership. status active is the one in force; superseded is an older filing. "
        "Term dates are calendar text, not timestamps.",
        {
            "id": _col("text", "Primary key."),
            "brand_id": _col("text", "brands.id."),
            "partnership_id": _col("text", "partnerships.id."),
            "title": _col("text", "Agreement title."),
            "file_name": _col("text", "Uploaded file name."),
            "file_sha256": _col("text", "Hash of the file bytes. Rarely useful in an answer."),
            "contract_type": _col("text", "Kind of agreement, as read from the document."),
            "status": _col("text", "active is in force.", ["active", "superseded"]),
            "effective_from": _col("text", "Calendar day YYYY-MM-DD the agreement takes effect, or null."),
            "term_start_date": _col("text", "Term start YYYY-MM-DD, or null."),
            "term_end_date": _col("text", "Term end YYYY-MM-DD, or null."),
            "auto_renewal": _col("boolean", "Whether the term renews on its own."),
            "exclusivity_type": _col("text", "Exclusivity as stated, or null."),
            "territory_text": _col("text", "Territory wording from the agreement."),
            "annual_sales_target": _col("jsonb", "Target object as read, or null."),
            "annual_sales_target_currency": _col("text", "Currency of the annual target.", ["USD"]),
            "created_at": _col("timestamptz", "When the row was filed, UTC."),
            "updated_at": _col("timestamptz", "When the row was last updated, UTC."),
        },
        where=_PARTNER,
        joins=["contract_discount_rules", "contract_territories"],
    ),
    "contract_extractions": _table(
        "One run of the contract reader. raw is the verbatim JSON. Prefer the normalized contract_* tables for figures.",
        {
            "id": _col("text", "Primary key."),
            "contract_id": _col("text", "contracts.id."),
            "model": _col("text", "Model that read the agreement."),
            "confidence_score": _col("numeric", "Reader confidence, 0 to 1."),
            "extraction_notes": _col("text", "Notes from the reader."),
            "raw": _col("jsonb", "Verbatim extraction. Large. Select only when the normalized tables are not enough."),
            "skipped": _col("jsonb", "List of strings the mapper could not place."),
            "extracted_at": _col("timestamptz", "When this run finished, UTC."),
        },
        where="c.partnership_id = :partnership_id",
        joins=["contracts"],
        source=_CONTRACT_CHILD.format(table="contract_extractions"),
        alias="t",
    ),
    "contract_discount_rules": _table(
        "A discount schedule on an agreement. Tiers are in contract_discount_tiers. "
        "applies_per and basis say what the schedule counts.",
        {
            "id": _col("integer", "Primary key."),
            "contract_id": _col("text", "contracts.id."),
            "position": _col("integer", "Order of the schedule in the agreement."),
            "title": _col("text", "Schedule title."),
            "section_reference": _col("text", "Clause or section label."),
            "applies_per": _col("text", "What period the schedule resets on.", ["each_contract_year", "calendar_year", "per_order"]),
            "basis": _col("text", "What is counted.", ["shipping_container_sequence", "purchase_volume"]),
            "source_clause_text": _col("text", "The clause the schedule was read from."),
        },
        where="c.partnership_id = :partnership_id",
        joins=["contracts", "contract_discount_tiers"],
        source=_CONTRACT_CHILD.format(table="contract_discount_rules"),
        alias="t",
    ),
    "contract_discount_tiers": _table(
        "One band of a discount schedule. to_container null means this band covers every further container. "
        "discount_percent 0 is list price.",
        {
            "id": _col("integer", "Primary key."),
            "rule_id": _col("integer", "contract_discount_rules.id."),
            "position": _col("integer", "Order of the band."),
            "from_container": _col("integer", "First container this band covers, 1-based. Null means from the first."),
            "to_container": _col("integer", "Last container this band covers, inclusive. Null means and all further."),
            "discount_percent": _col("numeric", "Percent off list price. 0 is list price."),
            "notes": _col("text", "Band notes."),
        },
        where="c.partnership_id = :partnership_id",
        joins=["contract_discount_rules"],
        source=(
            "public.contract_discount_tiers AS t "
            "JOIN public.contract_discount_rules AS r ON r.id = t.rule_id "
            "JOIN public.contracts AS c ON c.id = r.contract_id"
        ),
        alias="t",
    ),
    "contract_moqs": _table(
        "Minimum quantities stated in an agreement.",
        {
            "id": _col("integer", "Primary key."),
            "contract_id": _col("text", "contracts.id."),
            "quantity": _col("numeric", "Minimum quantity."),
            "unit": _col("text", "Unit of quantity.", ["cases", "units"]),
            "applies_per": _col("text", "What the minimum applies to.", ["per_order", "per_year"]),
            "product_scope": _col("text", "Which products, as text from the clause."),
            "sku": _col("text", "SKU when the minimum is for one product."),
            "currency": _col("text", "Currency when the minimum is a value."),
            "source_clause_text": _col("text", "The clause the minimum was read from."),
        },
        where="c.partnership_id = :partnership_id",
        joins=["contracts"],
        source=_CONTRACT_CHILD.format(table="contract_moqs"),
        alias="t",
    ),
    "contract_price_list": _table(
        "Prices named in an agreement. unit_price is the figure in the clause, not a computed case price.",
        {
            "id": _col("integer", "Primary key."),
            "contract_id": _col("text", "contracts.id."),
            "product_name": _col("text", "Product name as written in the agreement."),
            "product_sku": _col("text", "SKU as written in the agreement."),
            "unit_price": _col("numeric", "Price in the clause."),
            "currency": _col("text", "Currency of unit_price.", ["USD"]),
            "unit": _col("text", "What unit_price buys.", ["case", "unit"]),
            "list_price_reference": _col("text", "Which list the price refers to."),
            "product_specific_discount_text": _col("text", "Product-specific discount wording."),
            "source_clause_text": _col("text", "The clause the price was read from."),
        },
        where="c.partnership_id = :partnership_id",
        joins=["contracts"],
        source=_CONTRACT_CHILD.format(table="contract_price_list"),
        alias="t",
    ),
    "contract_territories": _table(
        "Countries an agreement allows or excludes.",
        {
            "id": _col("integer", "Primary key."),
            "contract_id": _col("text", "contracts.id."),
            "country_code": _col("text", "ISO alpha-2.", ["US", "DE"]),
            "kind": _col("text", "Whether the country is in the territory.", ["allowed", "excluded"]),
        },
        where="c.partnership_id = :partnership_id",
        joins=["contracts"],
        source=_CONTRACT_CHILD.format(table="contract_territories"),
        alias="t",
    ),
    "contract_evidence": _table(
        "Where in the document a contract field was read from.",
        {
            "id": _col("integer", "Primary key."),
            "contract_id": _col("text", "contracts.id."),
            "field": _col("text", "Which contract field this quote supports."),
            "page": _col("integer", "1-based page, or null."),
            "quote": _col("text", "The quoted passage."),
            "section_hint": _col("text", "Section heading, if any."),
            "char_start": _col("integer", "Character offset of the quote, or null."),
        },
        where="c.partnership_id = :partnership_id",
        joins=["contracts"],
        source=_CONTRACT_CHILD.format(table="contract_evidence"),
        alias="t",
    ),
    "contract_chunks": _table(
        "Heading tree of one agreement. body is the section text and can be long; filter by title or path.",
        {
            "id": _col("integer", "Primary key."),
            "contract_id": _col("text", "contracts.id."),
            "chunk_id": _col("text", "Id of this heading inside the agreement."),
            "parent_chunk_id": _col("text", "chunk_id of the parent heading, or null for a root."),
            "number": _col("text", "Heading number as printed, such as 3.2."),
            "title": _col("text", "Heading title."),
            "body": _col("text", "Section text. Long. Filter before selecting it."),
            "path": _col("text", "Ancestor titles joined into one path."),
            "page_start": _col("integer", "First page of the section."),
            "page_end": _col("integer", "Last page of the section."),
            "sibling_order": _col("integer", "Order among siblings."),
        },
        where="c.partnership_id = :partnership_id",
        joins=["contracts"],
        source=_CONTRACT_CHILD.format(table="contract_chunks"),
        alias="t",
    ),
    "purchase_orders": _table(
        "Submitted purchase orders for this partnership. quantity is not on this table; line quantity is cases on "
        "purchase_order_items. Money columns are already computed; sum them, do not re-price. "
        "eta_date is a calendar day. submitted_at is a UTC instant.",
        {
            "id": _col("text", "Primary key."),
            "po_number": _col("text", "Human order number.", ["PO-20261019-0001"]),
            "brand_id": _col("text", "brands.id."),
            "distributor_id": _col("text", "distributors.id."),
            "partnership_id": _col("text", "partnerships.id."),
            "status": _col(
                "text", "Where the order is in fulfillment.",
                ["submitted", "awaiting_payment", "payment_claimed", "completed", "awaiting_refund", "claim_filed"],
            ),
            "currency": _col("text", "Currency of the money columns.", ["USD"]),
            "payment_terms": _col("text", "How the order is paid.", ["50_50", "100_prepaid", "net_30", "net_60"]),
            "incoterms": _col("text", "Incoterm.", ["FOB", "CIF", "EXW", "DDP", "DAP", "CFR"]),
            "shipping_method": _col("text", "How it ships.", ["sea", "air", "express", "land"]),
            "eta_date": _col("text", "Estimated arrival YYYY-MM-DD, or null. Not a timestamp."),
            "ship_to_country": _col("text", "ISO alpha-2 ship-to country."),
            "notes": _col("text", "Free-text notes on the order."),
            "discount_amount": _col("numeric", "Discount in currency, or null when there is none."),
            "discount_percentage": _col("numeric", "Discount percent, or null when the discount is an amount or absent."),
            "subtotal_amount": _col("numeric", "Sum of line totals before discount."),
            "total_amount": _col("numeric", "subtotal_amount minus discount_amount."),
            "submitted_at": _col("timestamptz", "When the order was submitted, UTC, or null."),
            "submit_cycle_version": _col("integer", "How many times this order was submitted."),
            "prepaid_amount": _col("numeric", "Amount recorded as prepaid."),
            "balance_paid": _col("numeric", "Balance recorded as paid after delivery."),
            "created_at": _col("timestamptz", "When the row was created, UTC."),
            "updated_at": _col("timestamptz", "When the row was last updated, UTC."),
        },
        where=_PARTNER,
        joins=["purchase_order_items"],
    ),
    "purchase_order_items": _table(
        "One line of a purchase order. quantity is cases, not units. line_total is already quantity times case_price.",
        {
            "id": _col("integer", "Primary key."),
            "po_id": _col("text", "purchase_orders.id."),
            "product_id": _col("text", "products.id."),
            "sku": _col("text", "SKU copied onto the line."),
            "product_name": _col("text", "Name copied onto the line."),
            "quantity": _col("integer", "Number of cases."),
            "case_price": _col("numeric", "Price of one case on this line."),
            "line_total": _col("numeric", "quantity times case_price."),
            "case_pack": _col("integer", "Units per case at the time of the order."),
        },
        where="po.partnership_id = :partnership_id",
        joins=["purchase_orders"],
        source=_PO_CHILD.format(table="purchase_order_items"),
        alias="t",
    ),
    "po_rule_evaluations": _table(
        "Snapshot of rule results at submission. snapshot is a JSON list of per-rule outcomes.",
        {
            "id": _col("integer", "Primary key."),
            "po_id": _col("text", "purchase_orders.id."),
            "evaluation_context": _col("text", "When the snapshot was taken.", ["submission"]),
            "submit_cycle_version": _col("integer", "Which submission this snapshot belongs to."),
            "snapshot": _col("jsonb", "List of rule results. Large."),
            "created_at": _col("timestamptz", "When the snapshot was stored, UTC."),
        },
        where="po.partnership_id = :partnership_id",
        joins=["purchase_orders"],
        source=_PO_CHILD.format(table="po_rule_evaluations"),
        alias="t",
    ),
    "po_events": _table(
        "Things that happened to an order after it existed: submitted, received, payment, a damage claim.",
        {
            "id": _col("integer", "Primary key."),
            "po_id": _col("text", "purchase_orders.id."),
            "kind": _col("text", "Event name.", ["submitted", "received", "payment", "claim"]),
            "data": _col("jsonb", "Event payload. Keys depend on kind."),
            "created_at": _col("timestamptz", "When the event was recorded, UTC."),
        },
        where="po.partnership_id = :partnership_id",
        joins=["purchase_orders"],
        source=_PO_CHILD.format(table="po_events"),
        alias="t",
    ),
    "brand_documents": _table(
        "Files the brand uploaded (decks, catalogues). This is the file record, not the text. "
        "Page text is brand_document_pages. The original file bytes are not queryable.",
        {
            "id": _col("text", "Primary key."),
            "brand_id": _col("text", "brands.id."),
            "title": _col("text", "Title, or null."),
            "file_name": _col("text", "Uploaded file name."),
            "file_sha256": _col("text", "Hash of the file bytes."),
            "media_type": _col("text", "MIME type."),
            "size_bytes": _col("integer", "File size in bytes."),
            "page_count": _col("integer", "Number of pages."),
            "created_at": _col("timestamptz", "When the file was stored, UTC."),
            "updated_at": _col("timestamptz", "When the row was last updated, UTC."),
        },
        where=_BRAND,
        joins=["brand_document_pages"],
    ),
    "brand_document_pages": _table(
        "Text of one page of a brand document. text can be long; filter by document or page.",
        {
            "id": _col("integer", "Primary key."),
            "document_id": _col("text", "brand_documents.id."),
            "page_number": _col("integer", "1-based page number."),
            "text": _col("text", "The page's text. Long."),
        },
        where="d.brand_id = :brand_id",
        joins=["brand_documents"],
        source=(
            "public.brand_document_pages AS t "
            "JOIN public.brand_documents AS d ON d.id = t.document_id"
        ),
        alias="t",
    ),
    "sales_reports": _table(
        "A sell-out or inventory file from this distributor. status confirmed means a person accepted it. "
        "Period columns are calendar text (YYYY-MM-DD), not timestamps. The numbers are in report_facts.",
        {
            "id": _col("text", "Primary key."),
            "brand_id": _col("text", "brands.id."),
            "partnership_id": _col("text", "partnerships.id."),
            "title": _col("text", "Report title."),
            "file_name": _col("text", "Uploaded file name."),
            "file_sha256": _col("text", "Hash of the file bytes."),
            "media_type": _col("text", "MIME type."),
            "size_bytes": _col("integer", "File size in bytes."),
            "reporting_period": _col("text", "Period label.", ["2026-03", "2026-Q1", "2026"]),
            "period_start": _col("text", "First calendar day YYYY-MM-DD, or null."),
            "period_end": _col("text", "Last calendar day YYYY-MM-DD, or null."),
            "status": _col("text", "draft until a person confirms.", ["draft", "confirmed"]),
            "confirmed_at": _col("timestamptz", "When it was confirmed, UTC, or null."),
            "notes": _col("text", "Notes on the report."),
            "created_at": _col("timestamptz", "When the row was created, UTC."),
            "updated_at": _col("timestamptz", "When the row was last updated, UTC."),
        },
        where=_PARTNER,
        joins=["report_facts"],
    ),
    "report_extractions": _table(
        "How one read of a sales report found its columns. The numbers are in report_facts, not here.",
        {
            "id": _col("text", "Primary key."),
            "report_id": _col("text", "sales_reports.id."),
            "method": _col("text", "Which reader ran.", ["tidy_table", "ai_table_extractor", "manual"]),
            "model": _col("text", "Model name when a model located the columns."),
            "raw": _col("jsonb", "Column mapping. Large."),
            "skipped": _col("jsonb", "List of things the reader skipped."),
            "extracted_at": _col("timestamptz", "When this read finished, UTC."),
        },
        where="r.partnership_id = :partnership_id",
        joins=["sales_reports"],
        source=_REPORT_CHILD.format(table="report_extractions"),
        alias="t",
    ),
    "report_facts": _table(
        "One figure from a sales report. Prefer status confirmed unless the question asks about drafts. "
        "superseded facts were replaced by a later report for the same period; do not sum them with confirmed. "
        "period_start and period_end are calendar text YYYY-MM-DD. numeric_value is the figure.",
        {
            "id": _col("integer", "Primary key."),
            "report_id": _col("text", "sales_reports.id."),
            "extraction_id": _col("text", "report_extractions.id, or null."),
            "metric_key": _col("text", "What was measured.", ["units_sold", "revenue", "unit_price", "stock_on_hand"]),
            "fact_type": _col("text", "Family of the metric.", ["sales", "inventory", "marketing", "narrative"]),
            "channel": _col("text", "Sales channel, or null."),
            "sku": _col("text", "SKU the figure is for, or null."),
            "product_id": _col("text", "products.id when the SKU matched the catalog, else null."),
            "entity": _col("text", "Other subject, such as a retailer name."),
            "period_start": _col("text", "First calendar day YYYY-MM-DD, or null."),
            "period_end": _col("text", "Last calendar day YYYY-MM-DD, or null."),
            "numeric_value": _col("numeric", "The number. Null when the fact is text."),
            "text_value": _col("text", "Prose fact, or null when the fact is numeric."),
            "unit": _col("text", "Unit of numeric_value.", ["units"]),
            "currency": _col("text", "Currency when metric_key is money.", ["USD"]),
            "source_sheet": _col("text", "Sheet or page the cell came from."),
            "source_row": _col("integer", "1-based source row."),
            "source_col": _col("integer", "1-based source column."),
            "source_header": _col("text", "Column header verbatim."),
            "ai_derived": _col("boolean", "True when the figure was derived rather than read from a cell."),
            "status": _col("text", "confirmed is the live figure.", ["draft", "confirmed", "superseded"]),
            "created_at": _col("timestamptz", "When the fact was stored, UTC."),
        },
        where="r.partnership_id = :partnership_id",
        joins=["sales_reports", "products"],
        source=_REPORT_CHILD.format(table="report_facts"),
        alias="t",
    ),
    "memories": _table(
        "Notes kept about this partnership across chats: a preference, a fact, an instruction, an event. "
        "Only is_active rows are still in force.",
        {
            "id": _col("integer", "Primary key. Higher means newer."),
            "partnership_id": _col("text", "partnerships.id."),
            "kind": _col("text", "What kind of note.", ["preference", "fact", "instruction", "event"]),
            "content": _col("text", "The note itself."),
            "source": _col("text", "Who wrote it.", ["user", "chat", "system"]),
            "session_id": _col("text", "Chat it was learned in, or null."),
            "is_active": _col("boolean", "False means forgotten."),
            "last_recalled_at": _col("timestamptz", "When it was last shown to the desk, UTC, or null."),
            "created_at": _col("timestamptz", "When it was stored, UTC."),
            "updated_at": _col("timestamptz", "When it was last changed, UTC."),
        },
        where=_PARTNER,
    ),
}


def catalog_text() -> str:
    """One line per table, for choosing which tables a question needs."""
    return "\n".join(f"- {name}: {meta['description']}" for name, meta in TABLES.items())


def expand_tables(names: list[str], *, limit: int = MAX_TABLES) -> list[str]:
    """Keep known tables, then add join parents until `limit`. Unknown names are dropped."""
    chosen: list[str] = []
    queue = [name for name in names if isinstance(name, str)]
    while queue and len(chosen) < limit:
        name = queue.pop(0)
        if name in chosen or name not in TABLES:
            continue
        chosen.append(name)
        for extra in TABLES[name]["joins"]:
            if extra not in chosen:
                queue.append(extra)
    return chosen


def describe_tables(names: list[str]) -> str:
    """Column-level description for the SQL prompt. Names must already be in TABLES."""
    blocks: list[str] = []
    for name in names:
        meta = TABLES[name]
        lines = [f"TABLE {name} — {meta['description']}"]
        if meta["joins"]:
            lines.append("  often joined with: " + ", ".join(meta["joins"]))
        for column, info in meta["columns"].items():
            example = f" examples: {info['examples']}" if "examples" in info else ""
            lines.append(f"  {column} {info['column_type']} — {info['column_description']}{example}")
        blocks.append("\n".join(lines))
    return "\n\n".join(blocks)


def scope_views() -> list[tuple[str, str]]:
    """TEMP view SQL. Each view reads public.* with bound scope ids, so the user statement never sees other rows."""
    views: list[tuple[str, str]] = []
    for name, meta in TABLES.items():
        alias = meta.get("alias")
        prefix = f"{alias}." if alias else ""
        columns = ", ".join(f"{prefix}{column}" for column in meta["columns"])
        source = meta.get("from") or f"public.{name}"
        views.append((name, f"SELECT {columns} FROM {source} WHERE {meta['where']}"))
    return views
