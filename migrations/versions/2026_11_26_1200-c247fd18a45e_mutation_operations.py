"""Store idempotent Create and Push operations."""

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects.postgresql import JSONB

revision = "c247fd18a45e"
down_revision = "ce097b0c4df0"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "mutation_operations",
        sa.Column("key", sa.Uuid(), primary_key=True),
        sa.Column("kind", sa.String(16), nullable=False),
        sa.Column("request", JSONB(), nullable=False),
        sa.Column("result", JSONB(none_as_null=True)),
        sa.Column("alias", sa.Text()),
        sa.Column("offer_name", sa.Text()),
        sa.Column("cleanup_pending", sa.Boolean(), server_default=sa.text("false"), nullable=False),
        sa.Column("keitaro_campaign_id", sa.BigInteger()),
        sa.Column("pending_stream", sa.Integer()),
        sa.Column("geo_stream_id", sa.BigInteger()),
        sa.Column("offers_stream_id", sa.BigInteger()),
        sa.UniqueConstraint("alias", name="uq_mutation_operations_alias"),
    )


def downgrade() -> None:
    op.drop_table("mutation_operations")
