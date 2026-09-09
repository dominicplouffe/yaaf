"""Promote project billing credentials without changing their encrypted values.

Revision ID: 0034
Revises: 0033
"""
import sqlalchemy as sa
from alembic import op

from yaaf.core.migration_ops import add_column_if_missing, create_fk_if_missing

revision = "0034"
down_revision = "0033"
branch_labels = None
depends_on = None


def upgrade() -> None:
    add_column_if_missing("secrets", sa.Column("llm_mode", sa.String(16), nullable=True))
    bind = op.get_bind()
    # The old attachment API also accepted the Founder's platform secret.
    # Preserve those assignments without making future Founder rotations alter projects.
    # One copy per billing mode the attached projects were using: a credential
    # has exactly one mode now, and a project that metered tokens must not wake
    # up assigned to a copy that says dollars.
    founders = bind.execute(sa.text(
        "SELECT DISTINCT s.id AS source, p.billing_mode AS mode FROM secrets s "
        "JOIN projects p ON p.billing_secret_id = s.id WHERE s.project_id IS NULL "
        "AND s.name = 'platform-billing'"
    )).mappings().all()
    for founder in founders:
        from yaaf.core import ids

        credential_id = ids.new_id(ids.SECRET)
        mode = founder["mode"] or "api_key"
        bind.execute(sa.text(
            "INSERT INTO secrets (id, project_id, name, ciphertext, role_scopes, kind, "
            "created_at, llm_mode) SELECT :id, NULL, :name, ciphertext, '[]', 'billing', "
            "created_at, :mode FROM secrets WHERE id = :source"
        ), {"id": credential_id, "name": f"Migrated shared credential ({credential_id})",
            "mode": mode, "source": founder["source"]})
        bind.execute(sa.text("UPDATE projects SET billing_secret_id = :id "
                             "WHERE billing_secret_id = :source AND billing_mode = :mode"),
                     {"id": credential_id, "source": founder["source"], "mode": mode})
    rows = bind.execute(sa.text(
        "SELECT id, project_id, name, ciphertext FROM secrets "
        "WHERE kind = 'billing' AND (project_id IS NOT NULL OR name != 'platform-billing') "
        "AND llm_mode IS NULL"
    )).mappings().all()
    for row in rows:
        mode = bind.execute(sa.text(
            "SELECT billing_mode FROM projects WHERE billing_secret_id = :id LIMIT 1"
        ), {"id": row["id"]}).scalar()
        if mode is None:
            # Nobody was paying with it, so the value is the only witness to its
            # kind. A value this key cannot read is still a credential worth
            # keeping; it just defaults to the common kind rather than aborting
            # the whole revision over a guess.
            from yaaf.core.crypto import decrypt
            try:
                plaintext = decrypt(row["ciphertext"])
            except Exception:
                plaintext = ""
            mode = "subscription" if plaintext.startswith("sk-ant-oat") else "api_key"
        bind.execute(sa.text(
            "UPDATE secrets SET project_id = NULL, name = :name, llm_mode = :mode, "
            "role_scopes = '[]' WHERE id = :id"
        ), {"id": row["id"], "name": f"Migrated {row['name'][:60]} ({row['id']})", "mode": mode})
    op.execute("CREATE UNIQUE INDEX IF NOT EXISTS uq_llm_credential_name "
               "ON secrets (lower(name)) WHERE llm_mode IS NOT NULL")
    # Older databases allowed dangling references after project/secret cleanup.
    bind.execute(sa.text("UPDATE projects SET billing_secret_id = NULL WHERE "
                         "billing_secret_id IS NOT NULL AND billing_secret_id NOT IN "
                         "(SELECT id FROM secrets)"))
    create_fk_if_missing("fk_projects_billing_secret", "projects", "secrets",
                         ["billing_secret_id"], ["id"])


def downgrade() -> None:
    raise RuntimeError("Shared credentials cannot safely be returned to project-local storage")
