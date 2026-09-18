"""add dynamic products and order line items

Revision ID: 9d2e7a41c6b3
Revises: f34f4c65517f
Create Date: 2026-09-18
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = "9d2e7a41c6b3"
down_revision: Union[str, Sequence[str], None] = "f34f4c65517f"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    with op.batch_alter_table("inventory") as batch:
        batch.add_column(sa.Column("unit_cost", sa.Numeric(14, 2), nullable=True))
        batch.add_column(sa.Column("active", sa.Boolean(), nullable=False, server_default=sa.true()))

    connection = op.get_bind()
    inventory = sa.table(
        "inventory",
        sa.column("sku", sa.String),
        sa.column("unit_cost", sa.Numeric(14, 2)),
    )
    settings = sa.table(
        "settings",
        sa.column("key", sa.String),
        sa.column("value", sa.JSON),
    )
    for sku, default in (("honey", 44), ("date", 44)):
        stored = connection.execute(
            sa.select(settings.c.value).where(settings.c.key == f"{sku}_unit_cost")
        ).scalar_one_or_none()
        connection.execute(
            inventory.update().where(inventory.c.sku == sku).values(unit_cost=stored if stored is not None else default)
        )
    connection.execute(inventory.update().where(inventory.c.unit_cost.is_(None)).values(unit_cost=0))

    with op.batch_alter_table("inventory") as batch:
        batch.alter_column("unit_cost", existing_type=sa.Numeric(14, 2), nullable=False)
    op.create_index(op.f("ix_inventory_active"), "inventory", ["active"], unique=False)

    op.create_table(
        "order_items",
        sa.Column("order_id", sa.String(length=32), nullable=False),
        sa.Column("sku", sa.String(length=16), nullable=False),
        sa.Column("quantity", sa.Integer(), nullable=False),
        sa.Column("unit_cost", sa.Numeric(14, 2), nullable=False),
        sa.ForeignKeyConstraint(["order_id"], ["orders.order_id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["sku"], ["inventory.sku"]),
        sa.PrimaryKeyConstraint("order_id", "sku"),
    )
    op.create_index(op.f("ix_order_items_sku"), "order_items", ["sku"], unique=False)

    orders = sa.table(
        "orders",
        sa.column("order_id", sa.String),
        sa.column("honey_qty", sa.Integer),
        sa.column("date_qty", sa.Integer),
        sa.column("honey_unit_cost", sa.Numeric(14, 2)),
        sa.column("date_unit_cost", sa.Numeric(14, 2)),
    )
    order_items = sa.table(
        "order_items",
        sa.column("order_id", sa.String),
        sa.column("sku", sa.String),
        sa.column("quantity", sa.Integer),
        sa.column("unit_cost", sa.Numeric(14, 2)),
    )
    rows = connection.execute(sa.select(
        orders.c.order_id, orders.c.honey_qty, orders.c.date_qty,
        orders.c.honey_unit_cost, orders.c.date_unit_cost,
    )).mappings()
    values = []
    for row in rows:
        if row["honey_qty"]:
            values.append({
                "order_id": row["order_id"], "sku": "honey",
                "quantity": row["honey_qty"], "unit_cost": row["honey_unit_cost"],
            })
        if row["date_qty"]:
            values.append({
                "order_id": row["order_id"], "sku": "date",
                "quantity": row["date_qty"], "unit_cost": row["date_unit_cost"],
            })
    if values:
        connection.execute(order_items.insert(), values)


def downgrade() -> None:
    op.drop_index(op.f("ix_order_items_sku"), table_name="order_items")
    op.drop_table("order_items")
    op.drop_index(op.f("ix_inventory_active"), table_name="inventory")
    with op.batch_alter_table("inventory") as batch:
        batch.drop_column("active")
        batch.drop_column("unit_cost")
