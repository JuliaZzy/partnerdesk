"""initial schema — every table the agent reads or writes (see partnerdesk/db/orm.py)

Revision ID: 0001
Revises: 
Create Date: 2026-09-20 14:54:50.123948
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision = '0001'
down_revision = None
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table('brands',
    sa.Column('id', sa.String(length=64), nullable=False),
    sa.Column('name', sa.String(length=200), nullable=False),
    sa.Column('preferred_currency', sa.String(length=3), nullable=False),
    sa.Column('created_at', sa.String(length=32), nullable=False),
    sa.Column('updated_at', sa.String(length=32), nullable=False),
    sa.PrimaryKeyConstraint('id')
    )
    op.create_table('distributors',
    sa.Column('id', sa.String(length=64), nullable=False),
    sa.Column('name', sa.String(length=200), nullable=False),
    sa.Column('country', sa.String(length=2), nullable=True),
    sa.Column('created_at', sa.String(length=32), nullable=False),
    sa.Column('updated_at', sa.String(length=32), nullable=False),
    sa.PrimaryKeyConstraint('id')
    )
    op.create_table('brand_agent_settings',
    sa.Column('brand_id', sa.String(length=64), nullable=False),
    sa.Column('payment_terms', sa.String(length=16), nullable=True),
    sa.Column('incoterms', sa.String(length=8), nullable=True),
    sa.Column('shipping_method', sa.String(length=16), nullable=True),
    sa.Column('eta_days', sa.Integer(), nullable=True),
    sa.Column('operating_context', sa.Text(), nullable=True),
    sa.Column('min_po_confidence', sa.Float(), nullable=True),
    sa.Column('tone', sa.Text(), nullable=True),
    sa.Column('updated_at', sa.String(length=32), nullable=False),
    sa.ForeignKeyConstraint(['brand_id'], ['brands.id'], ),
    sa.PrimaryKeyConstraint('brand_id')
    )
    op.create_table('partnerships',
    sa.Column('id', sa.String(length=64), nullable=False),
    sa.Column('brand_id', sa.String(length=64), nullable=False),
    sa.Column('distributor_id', sa.String(length=64), nullable=False),
    sa.Column('ship_to_country', sa.String(length=2), nullable=True),
    sa.Column('currency', sa.String(length=3), nullable=False),
    sa.Column('created_at', sa.String(length=32), nullable=False),
    sa.Column('updated_at', sa.String(length=32), nullable=False),
    sa.ForeignKeyConstraint(['brand_id'], ['brands.id'], ),
    sa.ForeignKeyConstraint(['distributor_id'], ['distributors.id'], ),
    sa.PrimaryKeyConstraint('id'),
    sa.UniqueConstraint('brand_id', 'distributor_id', name='uq_partnership_pair')
    )
    with op.batch_alter_table('partnerships', schema=None) as batch_op:
        batch_op.create_index(batch_op.f('ix_partnerships_brand_id'), ['brand_id'], unique=False)
        batch_op.create_index(batch_op.f('ix_partnerships_distributor_id'), ['distributor_id'], unique=False)

    op.create_table('products',
    sa.Column('id', sa.String(length=64), nullable=False),
    sa.Column('brand_id', sa.String(length=64), nullable=False),
    sa.Column('sku', sa.String(length=64), nullable=False),
    sa.Column('product_name_en', sa.String(length=200), nullable=True),
    sa.Column('native_name', sa.String(length=200), nullable=True),
    sa.Column('category', sa.String(length=100), nullable=True),
    sa.Column('subcategory', sa.String(length=100), nullable=True),
    sa.Column('variant', sa.String(length=100), nullable=True),
    sa.Column('form', sa.String(length=100), nullable=True),
    sa.Column('unit_size', sa.String(length=50), nullable=True),
    sa.Column('net_content', sa.String(length=50), nullable=True),
    sa.Column('description_en', sa.Text(), nullable=True),
    sa.Column('case_pack', sa.Integer(), nullable=True),
    sa.Column('case_price', sa.Float(), nullable=True),
    sa.Column('moq_units', sa.Integer(), nullable=True),
    sa.Column('cases_per_layer', sa.Integer(), nullable=True),
    sa.Column('layers_per_pallet', sa.Integer(), nullable=True),
    sa.Column('cases_per_pallet', sa.Integer(), nullable=True),
    sa.Column('case_weight', sa.Float(), nullable=True),
    sa.Column('commercial_role', sa.String(length=32), nullable=True),
    sa.Column('discontinued', sa.Boolean(), nullable=False),
    sa.Column('policy_tags', sa.JSON().with_variant(postgresql.JSONB(astext_type=sa.Text()), 'postgresql'), nullable=False),
    sa.Column('restricted_territories', sa.JSON().with_variant(postgresql.JSONB(astext_type=sa.Text()), 'postgresql'), nullable=False),
    sa.Column('created_at', sa.String(length=32), nullable=False),
    sa.Column('updated_at', sa.String(length=32), nullable=False),
    sa.ForeignKeyConstraint(['brand_id'], ['brands.id'], ),
    sa.PrimaryKeyConstraint('id'),
    sa.UniqueConstraint('brand_id', 'sku', name='uq_product_sku_per_brand')
    )
    with op.batch_alter_table('products', schema=None) as batch_op:
        batch_op.create_index(batch_op.f('ix_products_brand_id'), ['brand_id'], unique=False)

    op.create_table('contracts',
    sa.Column('id', sa.String(length=64), nullable=False),
    sa.Column('brand_id', sa.String(length=64), nullable=False),
    sa.Column('partnership_id', sa.String(length=64), nullable=False),
    sa.Column('title', sa.String(length=200), nullable=True),
    sa.Column('file_name', sa.String(length=255), nullable=True),
    sa.Column('file_sha256', sa.String(length=64), nullable=True),
    sa.Column('contract_type', sa.String(length=64), nullable=True),
    sa.Column('status', sa.String(length=16), nullable=False),
    sa.Column('effective_from', sa.String(length=10), nullable=True),
    sa.Column('term_start_date', sa.String(length=10), nullable=True),
    sa.Column('term_end_date', sa.String(length=10), nullable=True),
    sa.Column('auto_renewal', sa.Boolean(), nullable=True),
    sa.Column('exclusivity_type', sa.String(length=64), nullable=True),
    sa.Column('territory_text', sa.Text(), nullable=True),
    sa.Column('annual_sales_target', sa.JSON().with_variant(postgresql.JSONB(astext_type=sa.Text()), 'postgresql'), nullable=True),
    sa.Column('annual_sales_target_currency', sa.String(length=8), nullable=True),
    sa.Column('created_at', sa.String(length=32), nullable=False),
    sa.Column('updated_at', sa.String(length=32), nullable=False),
    sa.ForeignKeyConstraint(['brand_id'], ['brands.id'], ),
    sa.ForeignKeyConstraint(['partnership_id'], ['partnerships.id'], ),
    sa.PrimaryKeyConstraint('id')
    )
    with op.batch_alter_table('contracts', schema=None) as batch_op:
        batch_op.create_index(batch_op.f('ix_contracts_brand_id'), ['brand_id'], unique=False)
        batch_op.create_index('ix_contracts_partnership_status', ['partnership_id', 'status'], unique=False)

    op.create_table('purchase_orders',
    sa.Column('id', sa.String(length=64), nullable=False),
    sa.Column('po_number', sa.String(length=32), nullable=False),
    sa.Column('brand_id', sa.String(length=64), nullable=False),
    sa.Column('distributor_id', sa.String(length=64), nullable=False),
    sa.Column('partnership_id', sa.String(length=64), nullable=False),
    sa.Column('status', sa.String(length=32), nullable=False),
    sa.Column('currency', sa.String(length=3), nullable=False),
    sa.Column('payment_terms', sa.String(length=16), nullable=False),
    sa.Column('incoterms', sa.String(length=8), nullable=False),
    sa.Column('shipping_method', sa.String(length=16), nullable=False),
    sa.Column('eta_date', sa.String(length=10), nullable=True),
    sa.Column('ship_to_country', sa.String(length=2), nullable=True),
    sa.Column('notes', sa.Text(), nullable=True),
    sa.Column('discount_amount', sa.Float(), nullable=True),
    sa.Column('discount_percentage', sa.Float(), nullable=True),
    sa.Column('subtotal_amount', sa.Float(), nullable=False),
    sa.Column('total_amount', sa.Float(), nullable=False),
    sa.Column('submitted_at', sa.String(length=32), nullable=True),
    sa.Column('submit_cycle_version', sa.Integer(), nullable=False),
    sa.Column('prepaid_amount', sa.Float(), nullable=False),
    sa.Column('balance_paid', sa.Float(), nullable=False),
    sa.Column('created_at', sa.String(length=32), nullable=False),
    sa.Column('updated_at', sa.String(length=32), nullable=False),
    sa.ForeignKeyConstraint(['brand_id'], ['brands.id'], ),
    sa.ForeignKeyConstraint(['distributor_id'], ['distributors.id'], ),
    sa.ForeignKeyConstraint(['partnership_id'], ['partnerships.id'], ),
    sa.PrimaryKeyConstraint('id'),
    sa.UniqueConstraint('po_number')
    )
    with op.batch_alter_table('purchase_orders', schema=None) as batch_op:
        batch_op.create_index(batch_op.f('ix_purchase_orders_brand_id'), ['brand_id'], unique=False)
        batch_op.create_index(batch_op.f('ix_purchase_orders_distributor_id'), ['distributor_id'], unique=False)
        batch_op.create_index(batch_op.f('ix_purchase_orders_partnership_id'), ['partnership_id'], unique=False)

    op.create_table('commercial_rules',
    sa.Column('id', sa.String(length=64), nullable=False),
    sa.Column('brand_id', sa.String(length=64), nullable=False),
    sa.Column('contract_id', sa.String(length=64), nullable=True),
    sa.Column('name', sa.String(length=200), nullable=False),
    sa.Column('rule_type', sa.String(length=64), nullable=False),
    sa.Column('rule_config', sa.JSON().with_variant(postgresql.JSONB(astext_type=sa.Text()), 'postgresql'), nullable=False),
    sa.Column('severity', sa.String(length=32), nullable=False),
    sa.Column('is_active', sa.Boolean(), nullable=False),
    sa.Column('partner_scope', sa.String(length=16), nullable=False),
    sa.Column('partner_ids', sa.JSON().with_variant(postgresql.JSONB(astext_type=sa.Text()), 'postgresql'), nullable=False),
    sa.Column('partner_regions', sa.JSON().with_variant(postgresql.JSONB(astext_type=sa.Text()), 'postgresql'), nullable=False),
    sa.Column('product_scope', sa.String(length=16), nullable=False),
    sa.Column('product_ids', sa.JSON().with_variant(postgresql.JSONB(astext_type=sa.Text()), 'postgresql'), nullable=False),
    sa.Column('product_roles', sa.JSON().with_variant(postgresql.JSONB(astext_type=sa.Text()), 'postgresql'), nullable=False),
    sa.Column('product_tags', sa.JSON().with_variant(postgresql.JSONB(astext_type=sa.Text()), 'postgresql'), nullable=False),
    sa.Column('effective_from', sa.String(length=10), nullable=True),
    sa.Column('effective_until', sa.String(length=10), nullable=True),
    sa.Column('created_at', sa.String(length=32), nullable=False),
    sa.Column('updated_at', sa.String(length=32), nullable=False),
    sa.ForeignKeyConstraint(['brand_id'], ['brands.id'], ),
    sa.ForeignKeyConstraint(['contract_id'], ['contracts.id'], ondelete='SET NULL'),
    sa.PrimaryKeyConstraint('id')
    )
    with op.batch_alter_table('commercial_rules', schema=None) as batch_op:
        batch_op.create_index(batch_op.f('ix_commercial_rules_brand_id'), ['brand_id'], unique=False)
        batch_op.create_index(batch_op.f('ix_commercial_rules_contract_id'), ['contract_id'], unique=False)

    op.create_table('contract_discount_rules',
    sa.Column('id', sa.Integer(), autoincrement=True, nullable=False),
    sa.Column('contract_id', sa.String(length=64), nullable=False),
    sa.Column('position', sa.Integer(), nullable=False),
    sa.Column('title', sa.String(length=200), nullable=True),
    sa.Column('section_reference', sa.String(length=100), nullable=True),
    sa.Column('applies_per', sa.String(length=32), nullable=True),
    sa.Column('basis', sa.String(length=32), nullable=True),
    sa.Column('source_clause_text', sa.Text(), nullable=True),
    sa.ForeignKeyConstraint(['contract_id'], ['contracts.id'], ondelete='CASCADE'),
    sa.PrimaryKeyConstraint('id')
    )
    with op.batch_alter_table('contract_discount_rules', schema=None) as batch_op:
        batch_op.create_index(batch_op.f('ix_contract_discount_rules_contract_id'), ['contract_id'], unique=False)

    op.create_table('contract_evidence',
    sa.Column('id', sa.Integer(), autoincrement=True, nullable=False),
    sa.Column('contract_id', sa.String(length=64), nullable=False),
    sa.Column('field', sa.String(length=100), nullable=False),
    sa.Column('page', sa.Integer(), nullable=True),
    sa.Column('quote', sa.Text(), nullable=False),
    sa.Column('section_hint', sa.String(length=200), nullable=True),
    sa.Column('char_start', sa.Integer(), nullable=True),
    sa.ForeignKeyConstraint(['contract_id'], ['contracts.id'], ondelete='CASCADE'),
    sa.PrimaryKeyConstraint('id')
    )
    with op.batch_alter_table('contract_evidence', schema=None) as batch_op:
        batch_op.create_index(batch_op.f('ix_contract_evidence_contract_id'), ['contract_id'], unique=False)

    op.create_table('contract_extractions',
    sa.Column('id', sa.String(length=64), nullable=False),
    sa.Column('contract_id', sa.String(length=64), nullable=False),
    sa.Column('model', sa.String(length=100), nullable=True),
    sa.Column('confidence_score', sa.Float(), nullable=True),
    sa.Column('extraction_notes', sa.Text(), nullable=True),
    sa.Column('raw', sa.JSON().with_variant(postgresql.JSONB(astext_type=sa.Text()), 'postgresql'), nullable=False),
    sa.Column('skipped', sa.JSON().with_variant(postgresql.JSONB(astext_type=sa.Text()), 'postgresql'), nullable=False),
    sa.Column('extracted_at', sa.String(length=32), nullable=False),
    sa.ForeignKeyConstraint(['contract_id'], ['contracts.id'], ondelete='CASCADE'),
    sa.PrimaryKeyConstraint('id')
    )
    with op.batch_alter_table('contract_extractions', schema=None) as batch_op:
        batch_op.create_index(batch_op.f('ix_contract_extractions_contract_id'), ['contract_id'], unique=False)

    op.create_table('contract_moqs',
    sa.Column('id', sa.Integer(), autoincrement=True, nullable=False),
    sa.Column('contract_id', sa.String(length=64), nullable=False),
    sa.Column('quantity', sa.Float(), nullable=True),
    sa.Column('unit', sa.String(length=32), nullable=True),
    sa.Column('applies_per', sa.String(length=32), nullable=True),
    sa.Column('product_scope', sa.String(length=200), nullable=True),
    sa.Column('sku', sa.String(length=64), nullable=True),
    sa.Column('currency', sa.String(length=8), nullable=True),
    sa.Column('source_clause_text', sa.Text(), nullable=True),
    sa.ForeignKeyConstraint(['contract_id'], ['contracts.id'], ondelete='CASCADE'),
    sa.PrimaryKeyConstraint('id')
    )
    with op.batch_alter_table('contract_moqs', schema=None) as batch_op:
        batch_op.create_index(batch_op.f('ix_contract_moqs_contract_id'), ['contract_id'], unique=False)

    op.create_table('contract_price_list',
    sa.Column('id', sa.Integer(), autoincrement=True, nullable=False),
    sa.Column('contract_id', sa.String(length=64), nullable=False),
    sa.Column('product_name', sa.String(length=200), nullable=True),
    sa.Column('product_sku', sa.String(length=64), nullable=True),
    sa.Column('unit_price', sa.Float(), nullable=True),
    sa.Column('currency', sa.String(length=8), nullable=True),
    sa.Column('unit', sa.String(length=32), nullable=True),
    sa.Column('list_price_reference', sa.String(length=200), nullable=True),
    sa.Column('product_specific_discount_text', sa.Text(), nullable=True),
    sa.Column('source_clause_text', sa.Text(), nullable=True),
    sa.ForeignKeyConstraint(['contract_id'], ['contracts.id'], ondelete='CASCADE'),
    sa.PrimaryKeyConstraint('id')
    )
    with op.batch_alter_table('contract_price_list', schema=None) as batch_op:
        batch_op.create_index(batch_op.f('ix_contract_price_list_contract_id'), ['contract_id'], unique=False)

    op.create_table('contract_territories',
    sa.Column('id', sa.Integer(), autoincrement=True, nullable=False),
    sa.Column('contract_id', sa.String(length=64), nullable=False),
    sa.Column('country_code', sa.String(length=2), nullable=False),
    sa.Column('kind', sa.String(length=16), nullable=False),
    sa.ForeignKeyConstraint(['contract_id'], ['contracts.id'], ondelete='CASCADE'),
    sa.PrimaryKeyConstraint('id'),
    sa.UniqueConstraint('contract_id', 'country_code', 'kind', name='uq_contract_territory')
    )
    with op.batch_alter_table('contract_territories', schema=None) as batch_op:
        batch_op.create_index(batch_op.f('ix_contract_territories_contract_id'), ['contract_id'], unique=False)

    op.create_table('po_events',
    sa.Column('id', sa.Integer(), autoincrement=True, nullable=False),
    sa.Column('po_id', sa.String(length=64), nullable=False),
    sa.Column('kind', sa.String(length=32), nullable=False),
    sa.Column('data', sa.JSON().with_variant(postgresql.JSONB(astext_type=sa.Text()), 'postgresql'), nullable=False),
    sa.Column('created_at', sa.String(length=32), nullable=False),
    sa.ForeignKeyConstraint(['po_id'], ['purchase_orders.id'], ondelete='CASCADE'),
    sa.PrimaryKeyConstraint('id')
    )
    with op.batch_alter_table('po_events', schema=None) as batch_op:
        batch_op.create_index(batch_op.f('ix_po_events_po_id'), ['po_id'], unique=False)

    op.create_table('po_rule_evaluations',
    sa.Column('id', sa.Integer(), autoincrement=True, nullable=False),
    sa.Column('po_id', sa.String(length=64), nullable=False),
    sa.Column('evaluation_context', sa.String(length=32), nullable=False),
    sa.Column('submit_cycle_version', sa.Integer(), nullable=False),
    sa.Column('snapshot', sa.JSON().with_variant(postgresql.JSONB(astext_type=sa.Text()), 'postgresql'), nullable=False),
    sa.Column('created_at', sa.String(length=32), nullable=False),
    sa.ForeignKeyConstraint(['po_id'], ['purchase_orders.id'], ondelete='CASCADE'),
    sa.PrimaryKeyConstraint('id')
    )
    with op.batch_alter_table('po_rule_evaluations', schema=None) as batch_op:
        batch_op.create_index(batch_op.f('ix_po_rule_evaluations_po_id'), ['po_id'], unique=False)

    op.create_table('purchase_order_items',
    sa.Column('id', sa.Integer(), autoincrement=True, nullable=False),
    sa.Column('po_id', sa.String(length=64), nullable=False),
    sa.Column('product_id', sa.String(length=64), nullable=False),
    sa.Column('sku', sa.String(length=64), nullable=False),
    sa.Column('product_name', sa.String(length=200), nullable=False),
    sa.Column('quantity', sa.Integer(), nullable=False),
    sa.Column('case_price', sa.Float(), nullable=False),
    sa.Column('line_total', sa.Float(), nullable=False),
    sa.Column('case_pack', sa.Integer(), nullable=True),
    sa.ForeignKeyConstraint(['po_id'], ['purchase_orders.id'], ondelete='CASCADE'),
    sa.ForeignKeyConstraint(['product_id'], ['products.id'], ),
    sa.PrimaryKeyConstraint('id')
    )
    with op.batch_alter_table('purchase_order_items', schema=None) as batch_op:
        batch_op.create_index(batch_op.f('ix_purchase_order_items_po_id'), ['po_id'], unique=False)

    op.create_table('sessions',
    sa.Column('id', sa.String(length=64), nullable=False),
    sa.Column('partnership_id', sa.String(length=64), nullable=False),
    sa.Column('specialist', sa.String(length=32), nullable=True),
    sa.Column('stage', sa.String(length=16), nullable=False),
    sa.Column('slots', sa.JSON().with_variant(postgresql.JSONB(astext_type=sa.Text()), 'postgresql'), nullable=False),
    sa.Column('history', sa.JSON().with_variant(postgresql.JSONB(astext_type=sa.Text()), 'postgresql'), nullable=False),
    sa.Column('confirm_token', sa.String(length=64), nullable=True),
    sa.Column('confirm_hash', sa.String(length=64), nullable=True),
    sa.Column('confirm_expires_at', sa.String(length=32), nullable=True),
    sa.Column('confirmed_at', sa.String(length=32), nullable=True),
    sa.Column('po_id', sa.String(length=64), nullable=True),
    sa.Column('created_at', sa.String(length=32), nullable=False),
    sa.Column('updated_at', sa.String(length=32), nullable=False),
    sa.ForeignKeyConstraint(['partnership_id'], ['partnerships.id'], ),
    sa.ForeignKeyConstraint(['po_id'], ['purchase_orders.id'], ondelete='SET NULL'),
    sa.PrimaryKeyConstraint('id')
    )
    with op.batch_alter_table('sessions', schema=None) as batch_op:
        batch_op.create_index(batch_op.f('ix_sessions_confirm_token'), ['confirm_token'], unique=False)
        batch_op.create_index(batch_op.f('ix_sessions_partnership_id'), ['partnership_id'], unique=False)

    op.create_table('contract_discount_tiers',
    sa.Column('id', sa.Integer(), autoincrement=True, nullable=False),
    sa.Column('rule_id', sa.Integer(), nullable=False),
    sa.Column('position', sa.Integer(), nullable=False),
    sa.Column('from_container', sa.Integer(), nullable=True),
    sa.Column('to_container', sa.Integer(), nullable=True),
    sa.Column('discount_percent', sa.Float(), nullable=True),
    sa.Column('notes', sa.Text(), nullable=True),
    sa.ForeignKeyConstraint(['rule_id'], ['contract_discount_rules.id'], ondelete='CASCADE'),
    sa.PrimaryKeyConstraint('id')
    )
    with op.batch_alter_table('contract_discount_tiers', schema=None) as batch_op:
        batch_op.create_index(batch_op.f('ix_contract_discount_tiers_rule_id'), ['rule_id'], unique=False)



def downgrade() -> None:
    with op.batch_alter_table('contract_discount_tiers', schema=None) as batch_op:
        batch_op.drop_index(batch_op.f('ix_contract_discount_tiers_rule_id'))

    op.drop_table('contract_discount_tiers')
    with op.batch_alter_table('sessions', schema=None) as batch_op:
        batch_op.drop_index(batch_op.f('ix_sessions_partnership_id'))
        batch_op.drop_index(batch_op.f('ix_sessions_confirm_token'))

    op.drop_table('sessions')
    with op.batch_alter_table('purchase_order_items', schema=None) as batch_op:
        batch_op.drop_index(batch_op.f('ix_purchase_order_items_po_id'))

    op.drop_table('purchase_order_items')
    with op.batch_alter_table('po_rule_evaluations', schema=None) as batch_op:
        batch_op.drop_index(batch_op.f('ix_po_rule_evaluations_po_id'))

    op.drop_table('po_rule_evaluations')
    with op.batch_alter_table('po_events', schema=None) as batch_op:
        batch_op.drop_index(batch_op.f('ix_po_events_po_id'))

    op.drop_table('po_events')
    with op.batch_alter_table('contract_territories', schema=None) as batch_op:
        batch_op.drop_index(batch_op.f('ix_contract_territories_contract_id'))

    op.drop_table('contract_territories')
    with op.batch_alter_table('contract_price_list', schema=None) as batch_op:
        batch_op.drop_index(batch_op.f('ix_contract_price_list_contract_id'))

    op.drop_table('contract_price_list')
    with op.batch_alter_table('contract_moqs', schema=None) as batch_op:
        batch_op.drop_index(batch_op.f('ix_contract_moqs_contract_id'))

    op.drop_table('contract_moqs')
    with op.batch_alter_table('contract_extractions', schema=None) as batch_op:
        batch_op.drop_index(batch_op.f('ix_contract_extractions_contract_id'))

    op.drop_table('contract_extractions')
    with op.batch_alter_table('contract_evidence', schema=None) as batch_op:
        batch_op.drop_index(batch_op.f('ix_contract_evidence_contract_id'))

    op.drop_table('contract_evidence')
    with op.batch_alter_table('contract_discount_rules', schema=None) as batch_op:
        batch_op.drop_index(batch_op.f('ix_contract_discount_rules_contract_id'))

    op.drop_table('contract_discount_rules')
    with op.batch_alter_table('commercial_rules', schema=None) as batch_op:
        batch_op.drop_index(batch_op.f('ix_commercial_rules_contract_id'))
        batch_op.drop_index(batch_op.f('ix_commercial_rules_brand_id'))

    op.drop_table('commercial_rules')
    with op.batch_alter_table('purchase_orders', schema=None) as batch_op:
        batch_op.drop_index(batch_op.f('ix_purchase_orders_partnership_id'))
        batch_op.drop_index(batch_op.f('ix_purchase_orders_distributor_id'))
        batch_op.drop_index(batch_op.f('ix_purchase_orders_brand_id'))

    op.drop_table('purchase_orders')
    with op.batch_alter_table('contracts', schema=None) as batch_op:
        batch_op.drop_index('ix_contracts_partnership_status')
        batch_op.drop_index(batch_op.f('ix_contracts_brand_id'))

    op.drop_table('contracts')
    with op.batch_alter_table('products', schema=None) as batch_op:
        batch_op.drop_index(batch_op.f('ix_products_brand_id'))

    op.drop_table('products')
    with op.batch_alter_table('partnerships', schema=None) as batch_op:
        batch_op.drop_index(batch_op.f('ix_partnerships_distributor_id'))
        batch_op.drop_index(batch_op.f('ix_partnerships_brand_id'))

    op.drop_table('partnerships')
    op.drop_table('brand_agent_settings')
    op.drop_table('distributors')
    op.drop_table('brands')
