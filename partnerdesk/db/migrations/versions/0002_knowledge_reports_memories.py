"""brand knowledge base, sales reports and long-term memory (see partnerdesk/db/orm.py)

Revision ID: 0002
Revises: 0001
Create Date: 2026-09-20 16:01:06.359230
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision = '0002'
down_revision = '0001'
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table('brand_documents',
    sa.Column('id', sa.String(length=64), nullable=False),
    sa.Column('brand_id', sa.String(length=64), nullable=False),
    sa.Column('title', sa.String(length=200), nullable=True),
    sa.Column('file_name', sa.String(length=255), nullable=False),
    sa.Column('file_sha256', sa.String(length=64), nullable=True),
    sa.Column('media_type', sa.String(length=100), nullable=True),
    sa.Column('size_bytes', sa.Integer(), nullable=True),
    sa.Column('page_count', sa.Integer(), nullable=True),
    sa.Column('pages_processed', sa.Integer(), nullable=True),
    sa.Column('summary', sa.Text(), nullable=True),
    sa.Column('model', sa.String(length=100), nullable=True),
    sa.Column('status', sa.String(length=16), nullable=False),
    sa.Column('distilled_at', sa.String(length=32), nullable=True),
    sa.Column('created_at', sa.String(length=32), nullable=False),
    sa.Column('updated_at', sa.String(length=32), nullable=False),
    sa.ForeignKeyConstraint(['brand_id'], ['brands.id'], ),
    sa.PrimaryKeyConstraint('id')
    )
    with op.batch_alter_table('brand_documents', schema=None) as batch_op:
        batch_op.create_index(batch_op.f('ix_brand_documents_brand_id'), ['brand_id'], unique=False)

    op.create_table('brand_document_pages',
    sa.Column('id', sa.Integer(), autoincrement=True, nullable=False),
    sa.Column('document_id', sa.String(length=64), nullable=False),
    sa.Column('page_number', sa.Integer(), nullable=False),
    sa.Column('text', sa.Text(), nullable=False),
    sa.ForeignKeyConstraint(['document_id'], ['brand_documents.id'], ondelete='CASCADE'),
    sa.PrimaryKeyConstraint('id'),
    sa.UniqueConstraint('document_id', 'page_number', name='uq_document_page')
    )
    with op.batch_alter_table('brand_document_pages', schema=None) as batch_op:
        batch_op.create_index(batch_op.f('ix_brand_document_pages_document_id'), ['document_id'], unique=False)

    op.create_table('brand_document_sections',
    sa.Column('id', sa.Integer(), autoincrement=True, nullable=False),
    sa.Column('document_id', sa.String(length=64), nullable=False),
    sa.Column('position', sa.Integer(), nullable=False),
    sa.Column('type', sa.String(length=64), nullable=False),
    sa.Column('label', sa.String(length=200), nullable=False),
    sa.Column('description', sa.Text(), nullable=True),
    sa.Column('page_numbers', sa.JSON().with_variant(postgresql.JSONB(astext_type=sa.Text()), 'postgresql'), nullable=False),
    sa.ForeignKeyConstraint(['document_id'], ['brand_documents.id'], ondelete='CASCADE'),
    sa.PrimaryKeyConstraint('id')
    )
    with op.batch_alter_table('brand_document_sections', schema=None) as batch_op:
        batch_op.create_index(batch_op.f('ix_brand_document_sections_document_id'), ['document_id'], unique=False)

    op.create_table('knowledge_fragments',
    sa.Column('id', sa.String(length=64), nullable=False),
    sa.Column('brand_id', sa.String(length=64), nullable=False),
    sa.Column('document_id', sa.String(length=64), nullable=True),
    sa.Column('product_id', sa.String(length=64), nullable=True),
    sa.Column('type', sa.String(length=64), nullable=False),
    sa.Column('title', sa.String(length=200), nullable=False),
    sa.Column('content', sa.Text(), nullable=False),
    sa.Column('product_name', sa.String(length=200), nullable=True),
    sa.Column('source_pages', sa.JSON().with_variant(postgresql.JSONB(astext_type=sa.Text()), 'postgresql'), nullable=False),
    sa.Column('tags', sa.JSON().with_variant(postgresql.JSONB(astext_type=sa.Text()), 'postgresql'), nullable=False),
    sa.Column('status', sa.String(length=16), nullable=False),
    sa.Column('created_at', sa.String(length=32), nullable=False),
    sa.Column('updated_at', sa.String(length=32), nullable=False),
    sa.ForeignKeyConstraint(['brand_id'], ['brands.id'], ),
    sa.ForeignKeyConstraint(['document_id'], ['brand_documents.id'], ondelete='SET NULL'),
    sa.ForeignKeyConstraint(['product_id'], ['products.id'], ondelete='SET NULL'),
    sa.PrimaryKeyConstraint('id')
    )
    with op.batch_alter_table('knowledge_fragments', schema=None) as batch_op:
        batch_op.create_index(batch_op.f('ix_knowledge_fragments_brand_id'), ['brand_id'], unique=False)
        batch_op.create_index('ix_knowledge_fragments_brand_type', ['brand_id', 'type'], unique=False)
        batch_op.create_index(batch_op.f('ix_knowledge_fragments_document_id'), ['document_id'], unique=False)
        batch_op.create_index(batch_op.f('ix_knowledge_fragments_product_id'), ['product_id'], unique=False)

    op.create_table('sales_reports',
    sa.Column('id', sa.String(length=64), nullable=False),
    sa.Column('brand_id', sa.String(length=64), nullable=False),
    sa.Column('partnership_id', sa.String(length=64), nullable=False),
    sa.Column('title', sa.String(length=200), nullable=True),
    sa.Column('file_name', sa.String(length=255), nullable=False),
    sa.Column('file_sha256', sa.String(length=64), nullable=True),
    sa.Column('media_type', sa.String(length=100), nullable=True),
    sa.Column('size_bytes', sa.Integer(), nullable=True),
    sa.Column('reporting_period', sa.String(length=10), nullable=True),
    sa.Column('period_start', sa.String(length=10), nullable=True),
    sa.Column('period_end', sa.String(length=10), nullable=True),
    sa.Column('status', sa.String(length=16), nullable=False),
    sa.Column('confirmed_at', sa.String(length=32), nullable=True),
    sa.Column('notes', sa.Text(), nullable=True),
    sa.Column('created_at', sa.String(length=32), nullable=False),
    sa.Column('updated_at', sa.String(length=32), nullable=False),
    sa.ForeignKeyConstraint(['brand_id'], ['brands.id'], ),
    sa.ForeignKeyConstraint(['partnership_id'], ['partnerships.id'], ),
    sa.PrimaryKeyConstraint('id')
    )
    with op.batch_alter_table('sales_reports', schema=None) as batch_op:
        batch_op.create_index(batch_op.f('ix_sales_reports_brand_id'), ['brand_id'], unique=False)
        batch_op.create_index('ix_sales_reports_partnership_period', ['partnership_id', 'period_start'], unique=False)

    op.create_table('report_extractions',
    sa.Column('id', sa.String(length=64), nullable=False),
    sa.Column('report_id', sa.String(length=64), nullable=False),
    sa.Column('method', sa.String(length=32), nullable=False),
    sa.Column('model', sa.String(length=100), nullable=True),
    sa.Column('raw', sa.JSON().with_variant(postgresql.JSONB(astext_type=sa.Text()), 'postgresql'), nullable=False),
    sa.Column('skipped', sa.JSON().with_variant(postgresql.JSONB(astext_type=sa.Text()), 'postgresql'), nullable=False),
    sa.Column('extracted_at', sa.String(length=32), nullable=False),
    sa.ForeignKeyConstraint(['report_id'], ['sales_reports.id'], ondelete='CASCADE'),
    sa.PrimaryKeyConstraint('id')
    )
    with op.batch_alter_table('report_extractions', schema=None) as batch_op:
        batch_op.create_index(batch_op.f('ix_report_extractions_report_id'), ['report_id'], unique=False)

    op.create_table('memories',
    sa.Column('id', sa.Integer(), autoincrement=True, nullable=False),
    sa.Column('partnership_id', sa.String(length=64), nullable=False),
    sa.Column('kind', sa.String(length=32), nullable=False),
    sa.Column('content', sa.Text(), nullable=False),
    sa.Column('source', sa.String(length=16), nullable=False),
    sa.Column('session_id', sa.String(length=64), nullable=True),
    sa.Column('is_active', sa.Boolean(), nullable=False),
    sa.Column('last_recalled_at', sa.String(length=32), nullable=True),
    sa.Column('created_at', sa.String(length=32), nullable=False),
    sa.Column('updated_at', sa.String(length=32), nullable=False),
    sa.ForeignKeyConstraint(['partnership_id'], ['partnerships.id'], ),
    sa.ForeignKeyConstraint(['session_id'], ['sessions.id'], ondelete='SET NULL'),
    sa.PrimaryKeyConstraint('id')
    )
    with op.batch_alter_table('memories', schema=None) as batch_op:
        batch_op.create_index('ix_memories_partnership_active', ['partnership_id', 'is_active'], unique=False)
        batch_op.create_index(batch_op.f('ix_memories_partnership_id'), ['partnership_id'], unique=False)

    op.create_table('report_facts',
    sa.Column('id', sa.Integer(), autoincrement=True, nullable=False),
    sa.Column('report_id', sa.String(length=64), nullable=False),
    sa.Column('extraction_id', sa.String(length=64), nullable=True),
    sa.Column('metric_key', sa.String(length=64), nullable=False),
    sa.Column('fact_type', sa.String(length=32), nullable=False),
    sa.Column('channel', sa.String(length=100), nullable=True),
    sa.Column('sku', sa.String(length=64), nullable=True),
    sa.Column('product_id', sa.String(length=64), nullable=True),
    sa.Column('entity', sa.String(length=200), nullable=True),
    sa.Column('period_start', sa.String(length=10), nullable=True),
    sa.Column('period_end', sa.String(length=10), nullable=True),
    sa.Column('numeric_value', sa.Float(), nullable=True),
    sa.Column('text_value', sa.Text(), nullable=True),
    sa.Column('unit', sa.String(length=32), nullable=True),
    sa.Column('currency', sa.String(length=8), nullable=True),
    sa.Column('source_sheet', sa.String(length=100), nullable=True),
    sa.Column('source_row', sa.Integer(), nullable=True),
    sa.Column('source_col', sa.Integer(), nullable=True),
    sa.Column('source_header', sa.String(length=200), nullable=True),
    sa.Column('ai_derived', sa.Boolean(), nullable=False),
    sa.Column('status', sa.String(length=16), nullable=False),
    sa.Column('created_at', sa.String(length=32), nullable=False),
    sa.ForeignKeyConstraint(['extraction_id'], ['report_extractions.id'], ondelete='SET NULL'),
    sa.ForeignKeyConstraint(['product_id'], ['products.id'], ondelete='SET NULL'),
    sa.ForeignKeyConstraint(['report_id'], ['sales_reports.id'], ondelete='CASCADE'),
    sa.PrimaryKeyConstraint('id')
    )
    with op.batch_alter_table('report_facts', schema=None) as batch_op:
        batch_op.create_index(batch_op.f('ix_report_facts_product_id'), ['product_id'], unique=False)
        batch_op.create_index(batch_op.f('ix_report_facts_report_id'), ['report_id'], unique=False)
        batch_op.create_index('ix_report_facts_report_status', ['report_id', 'status'], unique=False)
        batch_op.create_index('ix_report_facts_sku_period', ['sku', 'period_start'], unique=False)



def downgrade() -> None:
    with op.batch_alter_table('report_facts', schema=None) as batch_op:
        batch_op.drop_index('ix_report_facts_sku_period')
        batch_op.drop_index('ix_report_facts_report_status')
        batch_op.drop_index(batch_op.f('ix_report_facts_report_id'))
        batch_op.drop_index(batch_op.f('ix_report_facts_product_id'))

    op.drop_table('report_facts')
    with op.batch_alter_table('memories', schema=None) as batch_op:
        batch_op.drop_index(batch_op.f('ix_memories_partnership_id'))
        batch_op.drop_index('ix_memories_partnership_active')

    op.drop_table('memories')
    with op.batch_alter_table('report_extractions', schema=None) as batch_op:
        batch_op.drop_index(batch_op.f('ix_report_extractions_report_id'))

    op.drop_table('report_extractions')
    with op.batch_alter_table('sales_reports', schema=None) as batch_op:
        batch_op.drop_index('ix_sales_reports_partnership_period')
        batch_op.drop_index(batch_op.f('ix_sales_reports_brand_id'))

    op.drop_table('sales_reports')
    with op.batch_alter_table('knowledge_fragments', schema=None) as batch_op:
        batch_op.drop_index(batch_op.f('ix_knowledge_fragments_product_id'))
        batch_op.drop_index(batch_op.f('ix_knowledge_fragments_document_id'))
        batch_op.drop_index('ix_knowledge_fragments_brand_type')
        batch_op.drop_index(batch_op.f('ix_knowledge_fragments_brand_id'))

    op.drop_table('knowledge_fragments')
    with op.batch_alter_table('brand_document_sections', schema=None) as batch_op:
        batch_op.drop_index(batch_op.f('ix_brand_document_sections_document_id'))

    op.drop_table('brand_document_sections')
    with op.batch_alter_table('brand_document_pages', schema=None) as batch_op:
        batch_op.drop_index(batch_op.f('ix_brand_document_pages_document_id'))

    op.drop_table('brand_document_pages')
    with op.batch_alter_table('brand_documents', schema=None) as batch_op:
        batch_op.drop_index(batch_op.f('ix_brand_documents_brand_id'))

    op.drop_table('brand_documents')
