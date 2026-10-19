"""catalog: product status enum, native name, search indexes

- `native_name` -> `product_name_native`, renamed in place (autogenerate saw a drop + add,
  which would have thrown the names away).
- `commercial_role` + `discontinued` -> one `product_status` enum (正品 / 赠品 / 样品 / 停产).
  Existing rows keep their meaning: discontinued wins, then gift / sample, else regular.
- Trigram indexes on the product names, SKU and brand name, so a substring search over a
  catalog of hundreds of thousands of products doesn't scan the table. `pg_trgm` ships with
  Postgres and is a trusted extension, so the database owner can enable it.

Revision ID: 0004
Revises: 0003
Create Date: 2026-09-24 15:47:36.874613
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = '0004'
down_revision = '0003'
branch_labels = None
depends_on = None

STATUS = sa.Enum('正品', '赠品', '样品', '停产', name='product_status')


def _trigram(name: str, table: str, column: str) -> None:
    op.create_index(name, table, [column], unique=False, postgresql_using='gin', postgresql_ops={column: 'gin_trgm_ops'})


def upgrade() -> None:
    op.execute('CREATE EXTENSION IF NOT EXISTS pg_trgm')

    op.alter_column('products', 'native_name', new_column_name='product_name_native')

    STATUS.create(op.get_bind())
    op.add_column('products', sa.Column('product_status', STATUS, server_default='正品', nullable=False))
    op.execute(
        "UPDATE products SET product_status = CASE"
        " WHEN discontinued THEN '停产'"
        " WHEN lower(commercial_role) = 'gift' THEN '赠品'"
        " WHEN lower(commercial_role) = 'sample' THEN '样品'"
        " ELSE '正品' END::product_status"
    )
    op.drop_column('products', 'discontinued')
    op.drop_column('products', 'commercial_role')

    op.create_index(op.f('ix_products_product_status'), 'products', ['product_status'], unique=False)
    op.create_index(op.f('ix_products_category'), 'products', ['category'], unique=False)
    _trigram('ix_products_name_en_trgm', 'products', 'product_name_en')
    _trigram('ix_products_name_native_trgm', 'products', 'product_name_native')
    _trigram('ix_products_sku_trgm', 'products', 'sku')
    _trigram('ix_brands_name_trgm', 'brands', 'name')


def downgrade() -> None:
    op.drop_index('ix_brands_name_trgm', table_name='brands')
    op.drop_index('ix_products_sku_trgm', table_name='products')
    op.drop_index('ix_products_name_native_trgm', table_name='products')
    op.drop_index('ix_products_name_en_trgm', table_name='products')
    op.drop_index(op.f('ix_products_category'), table_name='products')
    op.drop_index(op.f('ix_products_product_status'), table_name='products')

    op.add_column('products', sa.Column('commercial_role', sa.VARCHAR(length=32), nullable=True))
    op.add_column('products', sa.Column('discontinued', sa.BOOLEAN(), server_default=sa.false(), nullable=False))
    op.execute(
        "UPDATE products SET"
        " discontinued = (product_status = '停产'),"
        " commercial_role = CASE product_status WHEN '赠品' THEN 'gift' WHEN '样品' THEN 'sample' ELSE 'regular' END"
    )
    op.alter_column('products', 'discontinued', server_default=None)
    op.drop_column('products', 'product_status')
    STATUS.drop(op.get_bind())

    op.alter_column('products', 'product_name_native', new_column_name='native_name')
    # pg_trgm stays: other databases on the server may use it, and it costs nothing idle.
