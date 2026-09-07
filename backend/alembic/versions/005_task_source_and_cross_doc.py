"""Task source_task_id, cross_doc_query enum, and safety_critical column

Revision ID: 005_task_source_and_cross_doc
Revises: 004_equipment_knowledge_graph
Create Date: 2026-09-07 09:14:00.000000

"""
from typing import Sequence, Union
from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

revision: str = '005_task_source_and_cross_doc'
down_revision: Union[str, Sequence[str], None] = '004_equipment_knowledge_graph'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

def upgrade() -> None:
    bind = op.get_bind()
    if bind.dialect.name == "postgresql":
        try:
            op.execute("ALTER TYPE tasktype ADD VALUE IF NOT EXISTS 'cross_doc_query';")
        except Exception:
            pass
        try:
            op.add_column('tasks', sa.Column('source_task_id', postgresql.UUID(as_uuid=True), sa.ForeignKey('tasks.id'), nullable=True))
        except Exception:
            pass
        try:
            op.add_column('memory_entries', sa.Column('safety_critical', sa.Boolean(), nullable=False, server_default='false'))
        except Exception:
            pass
    else:
        try:
            op.add_column('tasks', sa.Column('source_task_id', sa.Uuid(), sa.ForeignKey('tasks.id'), nullable=True))
        except Exception:
            pass
        try:
            op.add_column('memory_entries', sa.Column('safety_critical', sa.Boolean(), nullable=False, server_default='0'))
        except Exception:
            pass

def downgrade() -> None:
    op.drop_column('memory_entries', 'safety_critical')
    op.drop_column('tasks', 'source_task_id')
