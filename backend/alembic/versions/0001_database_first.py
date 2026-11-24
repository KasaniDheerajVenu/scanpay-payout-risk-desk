"""Create the eleven-table PostgreSQL schema and append-only write guards."""

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

from alembic import op

revision = "0001_database_first"
down_revision = None
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "model_versions",
        sa.Column(
            "model_version_id",
            sa.Uuid(),
            server_default=sa.text("gen_random_uuid()"),
            nullable=False,
        ),
        sa.Column("scope_key", sa.Text(), nullable=False),
        sa.Column("training_fingerprint", sa.Text(), nullable=False),
        sa.Column("algorithm", sa.Text(), nullable=False),
        sa.Column("feature_schema_version", sa.Text(), nullable=False),
        sa.Column("config", postgresql.JSONB(), nullable=False),
        sa.Column("fit_cutoff_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("validation_cutoff_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("training_observation_count", sa.Integer(), nullable=False),
        sa.Column("validation_observation_count", sa.Integer(), nullable=False),
        sa.Column("anomaly_threshold", sa.Float(precision=53), nullable=True),
        sa.Column(
            "metrics", postgresql.JSONB(), server_default=sa.text("'{}'::jsonb"), nullable=False
        ),
        sa.Column("status", sa.Text(), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.CheckConstraint(
            "status IN ('ACTIVE', 'DIAGNOSTIC_ONLY', 'INSUFFICIENT_HISTORY', "
            "'VALIDATION_FAILED', 'INFERENCE_FAILED')",
            name=op.f("ck_model_versions_status_values"),
        ),
        sa.CheckConstraint(
            "training_observation_count >= 0 AND validation_observation_count >= 0",
            name=op.f("ck_model_versions_nonnegative_counts"),
        ),
        sa.CheckConstraint(
            "validation_cutoff_at >= fit_cutoff_at",
            name=op.f("ck_model_versions_chronological_cutoffs"),
        ),
        sa.PrimaryKeyConstraint("model_version_id", name=op.f("pk_model_versions")),
        sa.UniqueConstraint(
            "scope_key",
            "training_fingerprint",
            "config",
            name=op.f("uq_model_versions_scope_key_training_fingerprint_config"),
        ),
    )
    op.create_table(
        "users",
        sa.Column(
            "user_id", sa.Uuid(), server_default=sa.text("gen_random_uuid()"), nullable=False
        ),
        sa.Column("username", sa.Text(), nullable=False),
        sa.Column("password_hash", sa.Text(), nullable=False),
        sa.Column("display_name", sa.Text(), nullable=False),
        sa.Column("role", sa.Text(), nullable=False),
        sa.Column("is_active", sa.Boolean(), server_default=sa.text("true"), nullable=False),
        sa.Column("auth_version", sa.Integer(), server_default=sa.text("1"), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.CheckConstraint(
            "password_hash ~ '[^[:space:]]'", name=op.f("ck_users_password_hash_nonblank")
        ),
        sa.CheckConstraint(
            "role IN ('ADMIN', 'ANALYST', 'VIEWER')", name=op.f("ck_users_role_values")
        ),
        sa.CheckConstraint("username ~ '[^[:space:]]'", name=op.f("ck_users_username_nonblank")),
        sa.CheckConstraint("auth_version >= 1", name=op.f("ck_users_auth_version_positive")),
        sa.PrimaryKeyConstraint("user_id", name=op.f("pk_users")),
        sa.UniqueConstraint("username", name=op.f("uq_users_username")),
    )
    op.create_table(
        "dataset_batches",
        sa.Column(
            "batch_id", sa.Uuid(), server_default=sa.text("gen_random_uuid()"), nullable=False
        ),
        sa.Column("scope_key", sa.Text(), nullable=False),
        sa.Column("uploaded_by", sa.Uuid(), nullable=False),
        sa.Column("mode", sa.Text(), nullable=False),
        sa.Column("cutoff_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("status", sa.Text(), server_default=sa.text("'UPLOADED'"), nullable=False),
        sa.Column("input_digest", sa.Text(), nullable=True),
        sa.Column("imported_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("error_summary", postgresql.JSONB(none_as_null=True), nullable=True),
        sa.CheckConstraint(
            "mode IN ('INITIAL', 'INCREMENTAL')", name=op.f("ck_dataset_batches_mode_values")
        ),
        sa.CheckConstraint(
            "scope_key ~ '[^[:space:]]'", name=op.f("ck_dataset_batches_scope_nonblank")
        ),
        sa.CheckConstraint(
            "status <> 'READY' OR (input_digest IS NOT NULL AND imported_at IS NOT NULL)",
            name=op.f("ck_dataset_batches_ready_has_import"),
        ),
        sa.CheckConstraint(
            "status IN ('UPLOADED', 'VALIDATING', 'READY', 'FAILED')",
            name=op.f("ck_dataset_batches_status_values"),
        ),
        sa.ForeignKeyConstraint(
            ["uploaded_by"], ["users.user_id"], name=op.f("fk_dataset_batches_uploaded_by_users")
        ),
        sa.PrimaryKeyConstraint("batch_id", name=op.f("pk_dataset_batches")),
    )
    op.create_index(
        "uq_dataset_batches_ready_scope_digest",
        "dataset_batches",
        ["scope_key", "input_digest"],
        unique=True,
        postgresql_where=sa.text("status = 'READY'"),
    )
    op.create_table(
        "evaluation_runs",
        sa.Column("run_id", sa.Uuid(), server_default=sa.text("gen_random_uuid()"), nullable=False),
        sa.Column("batch_id", sa.Uuid(), nullable=False),
        sa.Column("scope_key", sa.Text(), nullable=False),
        sa.Column("requested_by", sa.Uuid(), nullable=False),
        sa.Column("model_version_id", sa.Uuid(), nullable=True),
        sa.Column("cutoff_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("snapshot_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("input_digest", sa.Text(), nullable=True),
        sa.Column("rule_version", sa.Text(), nullable=False),
        sa.Column("status", sa.Text(), server_default=sa.text("'QUEUED'"), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column("started_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("completed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("error_summary", postgresql.JSONB(none_as_null=True), nullable=True),
        sa.Column(
            "history_manifest",
            postgresql.JSONB(),
            server_default=sa.text("'{}'::jsonb"),
            nullable=False,
        ),
        sa.Column(
            "summary", postgresql.JSONB(), server_default=sa.text("'{}'::jsonb"), nullable=False
        ),
        sa.CheckConstraint(
            "scope_key ~ '[^[:space:]]'", name=op.f("ck_evaluation_runs_scope_nonblank")
        ),
        sa.CheckConstraint(
            "status <> 'COMPLETED' OR (snapshot_at IS NOT NULL AND input_digest IS NOT "
            "NULL AND completed_at IS NOT NULL)",
            name=op.f("ck_evaluation_runs_completed_has_snapshot"),
        ),
        sa.CheckConstraint(
            "status IN ('QUEUED', 'RUNNING', 'COMPLETED', 'FAILED')",
            name=op.f("ck_evaluation_runs_status_values"),
        ),
        sa.ForeignKeyConstraint(
            ["batch_id"],
            ["dataset_batches.batch_id"],
            name=op.f("fk_evaluation_runs_batch_id_dataset_batches"),
        ),
        sa.ForeignKeyConstraint(
            ["model_version_id"],
            ["model_versions.model_version_id"],
            name=op.f("fk_evaluation_runs_model_version_id_model_versions"),
        ),
        sa.ForeignKeyConstraint(
            ["requested_by"], ["users.user_id"], name=op.f("fk_evaluation_runs_requested_by_users")
        ),
        sa.PrimaryKeyConstraint("run_id", name=op.f("pk_evaluation_runs")),
    )
    op.create_index(
        "ix_evaluation_runs_batch_status", "evaluation_runs", ["batch_id", "status"], unique=False
    )
    op.create_index(
        "uq_evaluation_runs_completed_batch_digest",
        "evaluation_runs",
        ["batch_id", "input_digest"],
        unique=True,
        postgresql_where=sa.text("status = 'COMPLETED'"),
    )
    op.create_table(
        "teams",
        sa.Column("team_id", sa.Text(), nullable=False),
        sa.Column("team_name", sa.Text(), nullable=False),
        sa.Column("profession", sa.Text(), nullable=False),
        sa.Column("owner_name", sa.Text(), nullable=False),
        sa.Column("owner_phone", sa.Text(), nullable=False),
        sa.Column("business_address", sa.Text(), nullable=False),
        sa.Column("city", sa.Text(), nullable=False),
        sa.Column("state", sa.Text(), nullable=False),
        sa.Column("onboarded_on", sa.Date(), nullable=False),
        sa.Column("status", sa.Text(), nullable=False),
        sa.Column("suspended_on", sa.Date(), nullable=True),
        sa.Column("payout_bank_routing", sa.Text(), nullable=False),
        sa.Column("payout_bank_last4", sa.Text(), nullable=False),
        sa.Column("signup_device_id", sa.Text(), nullable=True),
        sa.Column("first_import_batch_id", sa.Uuid(), nullable=False),
        sa.Column(
            "ingested_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.CheckConstraint(
            "profession IN ('Plumbing', 'Locksmith', 'HVAC', 'Electrical', 'Garage "
            "Door', 'Appliance Repair', 'Pest Control', 'Handyman')",
            name=op.f("ck_teams_profession_values"),
        ),
        sa.CheckConstraint(
            "status IN ('active', 'suspended')", name=op.f("ck_teams_status_values")
        ),
        sa.ForeignKeyConstraint(
            ["first_import_batch_id"],
            ["dataset_batches.batch_id"],
            name=op.f("fk_teams_first_import_batch_id_dataset_batches"),
        ),
        sa.PrimaryKeyConstraint("team_id", name=op.f("pk_teams")),
    )
    op.create_table(
        "uploaded_files",
        sa.Column(
            "file_id", sa.Uuid(), server_default=sa.text("gen_random_uuid()"), nullable=False
        ),
        sa.Column("batch_id", sa.Uuid(), nullable=False),
        sa.Column("file_type", sa.Text(), nullable=False),
        sa.Column("filename", sa.Text(), nullable=False),
        sa.Column("sha256", sa.Text(), nullable=False),
        sa.Column("bytes_count", sa.BigInteger(), nullable=False),
        sa.Column("row_count", sa.Integer(), nullable=False),
        sa.Column("inserted_count", sa.Integer(), nullable=False),
        sa.Column("unchanged_count", sa.Integer(), nullable=False),
        sa.Column(
            "validation_summary",
            postgresql.JSONB(),
            server_default=sa.text("'{}'::jsonb"),
            nullable=False,
        ),
        sa.Column(
            "imported_source_ids",
            postgresql.JSONB(),
            server_default=sa.text("'[]'::jsonb"),
            nullable=False,
        ),
        sa.CheckConstraint(
            "file_type IN ('teams', 'history_transactions', 'history_payouts', "
            "'latest_transactions', 'pending_payouts')",
            name=op.f("ck_uploaded_files_file_type_values"),
        ),
        sa.CheckConstraint(
            "filename ~ '[^[:space:]]' AND filename NOT IN ('.', '..') AND "
            "strpos(filename, '/') = 0 AND strpos(filename, chr(92)) = 0",
            name=op.f("ck_uploaded_files_sanitized_filename"),
        ),
        sa.CheckConstraint(
            "jsonb_typeof(imported_source_ids) = 'array'",
            name=op.f("ck_uploaded_files_membership_is_array"),
        ),
        sa.CheckConstraint(
            "sha256 ~ '^[0-9a-f]{64}$'", name=op.f("ck_uploaded_files_valid_sha256")
        ),
        sa.CheckConstraint(
            "bytes_count >= 0 AND row_count >= 0 AND inserted_count >= 0 AND "
            "unchanged_count >= 0 AND inserted_count + unchanged_count <= row_count",
            name=op.f("ck_uploaded_files_valid_counts"),
        ),
        sa.ForeignKeyConstraint(
            ["batch_id"],
            ["dataset_batches.batch_id"],
            name=op.f("fk_uploaded_files_batch_id_dataset_batches"),
        ),
        sa.PrimaryKeyConstraint("file_id", name=op.f("pk_uploaded_files")),
        sa.UniqueConstraint(
            "batch_id", "file_type", name=op.f("uq_uploaded_files_batch_id_file_type")
        ),
    )
    op.create_table(
        "payouts",
        sa.Column("payout_id", sa.Text(), nullable=False),
        sa.Column("team_id", sa.Text(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("created_date_ist", sa.Date(), nullable=False),
        sa.Column("covers_from", sa.Date(), nullable=False),
        sa.Column("covers_to", sa.Date(), nullable=False),
        sa.Column("txn_count", sa.Integer(), nullable=False),
        sa.Column("payout_amount_cents", sa.BigInteger(), nullable=False),
        sa.Column("historical_decision", sa.Text(), nullable=True),
        sa.Column("historical_decided_by", sa.Text(), nullable=True),
        sa.Column("historical_decided_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("historical_reason_code", sa.Text(), nullable=True),
        sa.Column("historical_reason", sa.Text(), nullable=True),
        sa.Column("source_status", sa.Text(), nullable=True),
        sa.Column("first_import_batch_id", sa.Uuid(), nullable=False),
        sa.Column("first_source_kind", sa.Text(), nullable=False),
        sa.Column("disposition_import_batch_id", sa.Uuid(), nullable=True),
        sa.Column("disposition_recorded_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column(
            "ingested_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.CheckConstraint(
            """
            (first_source_kind = 'HISTORY'
             AND source_status IS NULL
             AND historical_decision IS NOT NULL AND historical_decided_at IS NOT NULL
             AND disposition_import_batch_id IS NULL AND disposition_recorded_at IS NULL)
            OR
            (first_source_kind = 'PENDING'
             AND source_status IS NOT DISTINCT FROM 'PENDING_DECISION'
             AND (
                (historical_decision IS NULL AND historical_decided_at IS NULL
                 AND historical_decided_by IS NULL AND historical_reason_code IS NULL
                 AND historical_reason IS NULL AND disposition_import_batch_id IS NULL
                 AND disposition_recorded_at IS NULL)
                OR
                (historical_decision IS NOT NULL AND historical_decided_at IS NOT NULL
                 AND disposition_import_batch_id IS NOT NULL
                 AND disposition_recorded_at IS NOT NULL)
             ))
            """,
            name=op.f("ck_payouts_source_disposition"),
        ),
        sa.CheckConstraint(
            "created_date_ist = (created_at AT TIME ZONE 'Asia/Kolkata')::date",
            name=op.f("ck_payouts_ist_creation_date"),
        ),
        sa.CheckConstraint(
            "first_source_kind IN ('HISTORY', 'PENDING')",
            name=op.f("ck_payouts_source_kind_values"),
        ),
        sa.CheckConstraint(
            "historical_decision IN ('AUTO_APPROVED', 'MANUAL_APPROVED', 'REJECTED')",
            name=op.f("ck_payouts_historical_decision_values"),
        ),
        sa.CheckConstraint(
            "historical_reason_code IN ('OUTLIER_VERIFIED', 'VOLUME_SPIKE_VERIFIED', "
            "'CARD_TESTING_VICTIM', 'FRAUD_CARD_RING', 'FRAUD_CARD_TESTING', "
            "'FRAUD_ACCOUNT_TAKEOVER')",
            name=op.f("ck_payouts_historical_reason_code_values"),
        ),
        sa.CheckConstraint("covers_from <= covers_to", name=op.f("ck_payouts_valid_coverage")),
        sa.CheckConstraint(
            "historical_decided_at >= created_at",
            name=op.f("ck_payouts_historical_timestamp_order"),
        ),
        sa.CheckConstraint("txn_count >= 0", name=op.f("ck_payouts_nonnegative_transaction_count")),
        sa.ForeignKeyConstraint(
            ["disposition_import_batch_id"],
            ["dataset_batches.batch_id"],
            name=op.f("fk_payouts_disposition_import_batch_id_dataset_batches"),
        ),
        sa.ForeignKeyConstraint(
            ["first_import_batch_id"],
            ["dataset_batches.batch_id"],
            name=op.f("fk_payouts_first_import_batch_id_dataset_batches"),
        ),
        sa.ForeignKeyConstraint(
            ["team_id"], ["teams.team_id"], name=op.f("fk_payouts_team_id_teams")
        ),
        sa.PrimaryKeyConstraint("payout_id", name=op.f("pk_payouts")),
    )
    op.create_index("ix_payouts_created_date_ist", "payouts", ["created_date_ist"], unique=False)
    op.create_index(
        "ix_payouts_historical_decided_at",
        "payouts",
        ["historical_decided_at"],
        unique=False,
        postgresql_where=sa.text("historical_decided_at IS NOT NULL"),
    )
    op.create_index(
        "ix_payouts_team_coverage", "payouts", ["team_id", "covers_from", "covers_to"], unique=False
    )
    op.create_table(
        "transactions",
        sa.Column("txn_id", sa.Text(), nullable=False),
        sa.Column("team_id", sa.Text(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("collection_date_ist", sa.Date(), nullable=False),
        sa.Column("amount_cents", sa.BigInteger(), nullable=False),
        sa.Column("tip_cents", sa.BigInteger(), nullable=False),
        sa.Column("payment_method", sa.Text(), nullable=False),
        sa.Column("card_fingerprint", sa.Text(), nullable=True),
        sa.Column("card_country", sa.Text(), nullable=True),
        sa.Column("status", sa.Text(), nullable=False),
        sa.Column("customer_name", sa.Text(), nullable=False),
        sa.Column("customer_zip", sa.Text(), nullable=False),
        sa.Column("first_import_batch_id", sa.Uuid(), nullable=False),
        sa.Column(
            "ingested_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.CheckConstraint(
            "collection_date_ist = (created_at AT TIME ZONE 'Asia/Kolkata')::date",
            name=op.f("ck_transactions_ist_collection_date"),
        ),
        sa.CheckConstraint(
            "payment_method IN ('card', 'tap_to_pay', 'ach')",
            name=op.f("ck_transactions_payment_method_values"),
        ),
        sa.CheckConstraint(
            "status IN ('captured', 'declined', 'refunded')",
            name=op.f("ck_transactions_status_values"),
        ),
        sa.ForeignKeyConstraint(
            ["first_import_batch_id"],
            ["dataset_batches.batch_id"],
            name=op.f("fk_transactions_first_import_batch_id_dataset_batches"),
        ),
        sa.ForeignKeyConstraint(
            ["team_id"], ["teams.team_id"], name=op.f("fk_transactions_team_id_teams")
        ),
        sa.PrimaryKeyConstraint("txn_id", name=op.f("pk_transactions")),
    )
    op.create_index(
        "ix_transactions_card_fingerprint",
        "transactions",
        ["card_fingerprint"],
        unique=False,
        postgresql_where=sa.text("card_fingerprint IS NOT NULL"),
    )
    op.create_index(
        "ix_transactions_team_collection_date",
        "transactions",
        ["team_id", "collection_date_ist"],
        unique=False,
    )
    op.create_table(
        "fraud_clusters",
        sa.Column(
            "cluster_id", sa.Uuid(), server_default=sa.text("gen_random_uuid()"), nullable=False
        ),
        sa.Column("stable_case_key", sa.Text(), nullable=False),
        sa.Column("run_id", sa.Uuid(), nullable=False),
        sa.Column("seed_payout_id", sa.Text(), nullable=False),
        sa.Column("reason_code", sa.Text(), nullable=False),
        sa.Column("summary", sa.Text(), nullable=False),
        sa.Column(
            "evidence", postgresql.JSONB(), server_default=sa.text("'[]'::jsonb"), nullable=False
        ),
        sa.CheckConstraint(
            "jsonb_typeof(evidence) = 'array'", name=op.f("ck_fraud_clusters_evidence_is_array")
        ),
        sa.CheckConstraint(
            "reason_code IN ('FRAUD_CARD_RING', 'FRAUD_CARD_TESTING', 'FRAUD_ACCOUNT_TAKEOVER')",
            name=op.f("ck_fraud_clusters_fraud_reason_values"),
        ),
        sa.ForeignKeyConstraint(
            ["run_id"],
            ["evaluation_runs.run_id"],
            name=op.f("fk_fraud_clusters_run_id_evaluation_runs"),
        ),
        sa.ForeignKeyConstraint(
            ["seed_payout_id"],
            ["payouts.payout_id"],
            name=op.f("fk_fraud_clusters_seed_payout_id_payouts"),
        ),
        sa.PrimaryKeyConstraint("cluster_id", name=op.f("pk_fraud_clusters")),
        sa.UniqueConstraint(
            "run_id", "cluster_id", name=op.f("uq_fraud_clusters_run_id_cluster_id")
        ),
        sa.UniqueConstraint(
            "run_id", "stable_case_key", name=op.f("uq_fraud_clusters_run_id_stable_case_key")
        ),
    )
    op.create_table(
        "payout_decisions",
        sa.Column(
            "decision_id", sa.Uuid(), server_default=sa.text("gen_random_uuid()"), nullable=False
        ),
        sa.Column("run_id", sa.Uuid(), nullable=False),
        sa.Column("payout_id", sa.Text(), nullable=False),
        sa.Column("model_version_id", sa.Uuid(), nullable=True),
        sa.Column("decision", sa.Text(), nullable=False),
        sa.Column("cluster_id", sa.Uuid(), nullable=True),
        sa.Column("reason", sa.Text(), nullable=False),
        sa.Column(
            "evidence", postgresql.JSONB(), server_default=sa.text("'[]'::jsonb"), nullable=False
        ),
        sa.Column(
            "findings", postgresql.JSONB(), server_default=sa.text("'{}'::jsonb"), nullable=False
        ),
        sa.Column("ml_score", sa.Float(precision=53), nullable=True),
        sa.Column("ml_is_anomaly", sa.Boolean(), nullable=True),
        sa.Column("ml_status", sa.Text(), nullable=False),
        sa.Column("rule_version", sa.Text(), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.CheckConstraint(
            "(decision = 'REJECT') = (cluster_id IS NOT NULL)",
            name=op.f("ck_payout_decisions_reject_has_cluster"),
        ),
        sa.CheckConstraint(
            "decision IN ('APPROVE', 'MANUAL_REVIEW', 'REJECT')",
            name=op.f("ck_payout_decisions_decision_values"),
        ),
        sa.CheckConstraint(
            "jsonb_typeof(evidence) = 'array'", name=op.f("ck_payout_decisions_evidence_is_array")
        ),
        sa.CheckConstraint(
            "ml_status IN ('ACTIVE', 'DIAGNOSTIC_ONLY', 'INSUFFICIENT_HISTORY', "
            "'VALIDATION_FAILED', 'INFERENCE_FAILED')",
            name=op.f("ck_payout_decisions_ml_status_values"),
        ),
        sa.CheckConstraint(
            "reason ~ '[^[:space:]]'", name=op.f("ck_payout_decisions_reason_nonblank")
        ),
        sa.CheckConstraint(
            "ml_is_anomaly IS NULL OR ml_score IS NOT NULL",
            name=op.f("ck_payout_decisions_anomaly_has_score"),
        ),
        sa.ForeignKeyConstraint(
            ["model_version_id"],
            ["model_versions.model_version_id"],
            name=op.f("fk_payout_decisions_model_version_id_model_versions"),
        ),
        sa.ForeignKeyConstraint(
            ["payout_id"], ["payouts.payout_id"], name=op.f("fk_payout_decisions_payout_id_payouts")
        ),
        sa.ForeignKeyConstraint(
            ["run_id", "cluster_id"],
            ["fraud_clusters.run_id", "fraud_clusters.cluster_id"],
            name=op.f("fk_payout_decisions_run_id_cluster_id_fraud_clusters"),
        ),
        sa.ForeignKeyConstraint(
            ["run_id"],
            ["evaluation_runs.run_id"],
            name=op.f("fk_payout_decisions_run_id_evaluation_runs"),
        ),
        sa.PrimaryKeyConstraint("decision_id", name=op.f("pk_payout_decisions")),
        sa.UniqueConstraint(
            "run_id", "payout_id", name=op.f("uq_payout_decisions_run_id_payout_id")
        ),
    )
    op.create_table(
        "payout_decision_history",
        sa.Column(
            "history_id", sa.Uuid(), server_default=sa.text("gen_random_uuid()"), nullable=False
        ),
        sa.Column("decision_id", sa.Uuid(), nullable=False),
        sa.Column("scope_key", sa.Text(), nullable=False),
        sa.Column("version", sa.Integer(), nullable=False),
        sa.Column("previous_decision", sa.Text(), nullable=False),
        sa.Column("new_decision", sa.Text(), nullable=False),
        sa.Column("cluster_id", sa.Uuid(), nullable=True),
        sa.Column("change_reason", sa.Text(), nullable=False),
        sa.Column(
            "evidence", postgresql.JSONB(), server_default=sa.text("'[]'::jsonb"), nullable=False
        ),
        sa.Column("changed_by", sa.Uuid(), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.CheckConstraint(
            "(new_decision = 'REJECT') = (cluster_id IS NOT NULL)",
            name=op.f("ck_payout_decision_history_reject_has_cluster"),
        ),
        sa.CheckConstraint(
            "change_reason ~ '[^[:space:]]'",
            name=op.f("ck_payout_decision_history_reason_nonblank"),
        ),
        sa.CheckConstraint(
            "jsonb_typeof(evidence) = 'array'",
            name=op.f("ck_payout_decision_history_evidence_is_array"),
        ),
        sa.CheckConstraint(
            "new_decision IN ('APPROVE', 'MANUAL_REVIEW', 'REJECT')",
            name=op.f("ck_payout_decision_history_new_decision_values"),
        ),
        sa.CheckConstraint(
            "previous_decision IN ('APPROVE', 'MANUAL_REVIEW', 'REJECT')",
            name=op.f("ck_payout_decision_history_previous_decision_values"),
        ),
        sa.CheckConstraint(
            "scope_key ~ '[^[:space:]]'", name=op.f("ck_payout_decision_history_scope_nonblank")
        ),
        sa.CheckConstraint("version >= 2", name=op.f("ck_payout_decision_history_analyst_version")),
        sa.ForeignKeyConstraint(
            ["changed_by"],
            ["users.user_id"],
            name=op.f("fk_payout_decision_history_changed_by_users"),
        ),
        sa.ForeignKeyConstraint(
            ["cluster_id"],
            ["fraud_clusters.cluster_id"],
            name=op.f("fk_payout_decision_history_cluster_id_fraud_clusters"),
        ),
        sa.ForeignKeyConstraint(
            ["decision_id"],
            ["payout_decisions.decision_id"],
            name=op.f("fk_payout_decision_history_decision_id_payout_decisions"),
        ),
        sa.PrimaryKeyConstraint("history_id", name=op.f("pk_payout_decision_history")),
        sa.UniqueConstraint(
            "decision_id",
            "scope_key",
            "version",
            name=op.f("uq_payout_decision_history_decision_id_scope_key_version"),
        ),
    )
    add_write_guards()


IMMUTABLE_TABLES = ("fraud_clusters", "payout_decisions", "payout_decision_history")


def add_write_guards() -> None:
    op.execute("""
        CREATE FUNCTION scanpay_reject_mutation() RETURNS trigger
        LANGUAGE plpgsql AS $$
        BEGIN
            RAISE EXCEPTION '% is immutable', TG_TABLE_NAME USING ERRCODE = '55000';
        END;
        $$
    """)
    for table in IMMUTABLE_TABLES:
        op.execute(
            f"CREATE TRIGGER {table}_immutable_rows BEFORE UPDATE OR DELETE ON {table} "
            "FOR EACH ROW EXECUTE FUNCTION scanpay_reject_mutation()"
        )
        op.execute(
            f"CREATE TRIGGER {table}_immutable_truncate BEFORE TRUNCATE ON {table} "
            "FOR EACH STATEMENT EXECUTE FUNCTION scanpay_reject_mutation()"
        )

    op.execute("""
        CREATE FUNCTION scanpay_guard_payout_disposition() RETURNS trigger
        LANGUAGE plpgsql AS $$
        DECLARE
            disposition_fields constant text[] := ARRAY[
                'historical_decision', 'historical_decided_by', 'historical_decided_at',
                'historical_reason_code', 'historical_reason',
                'disposition_import_batch_id', 'disposition_recorded_at'
            ];
        BEGIN
            IF TG_OP = 'INSERT' THEN
                IF NEW.first_source_kind = 'PENDING' AND NEW.historical_decision IS NOT NULL THEN
                    RAISE EXCEPTION 'A pending payout must be imported before disposition'
                        USING ERRCODE = '23514';
                END IF;
                RETURN NEW;
            END IF;
            IF NEW IS NOT DISTINCT FROM OLD THEN
                RETURN NEW;
            END IF;
            IF OLD.first_source_kind <> 'PENDING' OR OLD.historical_decision IS NOT NULL
                OR NEW.historical_decision IS NULL
                OR (to_jsonb(NEW) - disposition_fields)
                    IS DISTINCT FROM (to_jsonb(OLD) - disposition_fields)
            THEN
                RAISE EXCEPTION 'Only first disposition of a pending payout can be recorded'
                    USING ERRCODE = '55000';
            END IF;
            RETURN NEW;
        END;
        $$
    """)
    op.execute("""
        CREATE TRIGGER payouts_disposition_guard BEFORE INSERT OR UPDATE ON payouts
        FOR EACH ROW EXECUTE FUNCTION scanpay_guard_payout_disposition()
    """)

    op.execute("""
        CREATE FUNCTION scanpay_guard_decision_history() RETURNS trigger
        LANGUAGE plpgsql AS $$
        DECLARE
            original payout_decisions%ROWTYPE;
            latest payout_decision_history%ROWTYPE;
            last_version integer;
            last_decision text;
        BEGIN
            SELECT * INTO original FROM payout_decisions
                WHERE decision_id = NEW.decision_id FOR UPDATE;
            IF NOT FOUND THEN
                RETURN NEW;
            END IF;
            IF NEW.cluster_id IS NOT NULL AND NOT EXISTS (
                SELECT 1 FROM fraud_clusters
                WHERE cluster_id = NEW.cluster_id AND run_id = original.run_id
            ) THEN
                RAISE EXCEPTION 'Override cluster must be from the same evaluation run'
                    USING ERRCODE = '23503';
            END IF;
            SELECT * INTO latest FROM payout_decision_history
                WHERE decision_id = NEW.decision_id AND scope_key = NEW.scope_key
                ORDER BY version DESC LIMIT 1;
            last_version := COALESCE(latest.version, 1);
            last_decision := COALESCE(latest.new_decision, original.decision);
            IF NEW.version > last_version + 1 OR (
                NEW.version = last_version + 1
                AND NEW.previous_decision IS DISTINCT FROM last_decision
            ) THEN
                RAISE EXCEPTION 'Override must follow the latest decision in its scope'
                    USING ERRCODE = '23514';
            END IF;
            RETURN NEW;
        END;
        $$
    """)
    op.execute("""
        CREATE TRIGGER payout_decision_history_append_version
        BEFORE INSERT ON payout_decision_history
        FOR EACH ROW EXECUTE FUNCTION scanpay_guard_decision_history()
    """)


def downgrade() -> None:
    op.drop_table("payout_decision_history")
    op.drop_table("payout_decisions")
    op.drop_table("fraud_clusters")
    op.drop_table("transactions")
    op.drop_table("payouts")
    op.drop_table("uploaded_files")
    op.drop_table("teams")
    op.drop_table("evaluation_runs")
    op.drop_table("dataset_batches")
    op.drop_table("users")
    op.drop_table("model_versions")
    op.execute("DROP FUNCTION scanpay_guard_decision_history()")
    op.execute("DROP FUNCTION scanpay_guard_payout_disposition()")
    op.execute("DROP FUNCTION scanpay_reject_mutation()")
