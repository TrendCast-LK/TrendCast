-- =============================================================================
-- Admin Dashboard
-- File: backend/schema/init/03_admin.sql
-- Purpose: Accounts for the admin dashboard (separate from app users, with
--          their own login) and an audit log of every admin action.
--          users.is_active, which admins toggle, lives in 01_app_backend.sql.
-- =============================================================================

-- =============================================================================
-- TABLE: admins
-- One row per admin account. Created only from the CLI
-- (python -m tools.create_admin); there is no signup route. Emails are stored
-- lower-cased.
-- =============================================================================
CREATE TABLE IF NOT EXISTS admins (
    id                      BIGSERIAL       PRIMARY KEY,
    full_name               VARCHAR(255)    NOT NULL,
    email                   VARCHAR(255)    NOT NULL UNIQUE,
    password_hash           VARCHAR(255)    NOT NULL,
    is_active               BOOLEAN         NOT NULL DEFAULT TRUE,
    last_login_at           TIMESTAMPTZ,
    created_at              TIMESTAMPTZ     NOT NULL DEFAULT NOW(),

    CONSTRAINT chk_admins_email_lowercase CHECK (email = LOWER(email))
);

COMMENT ON TABLE admins IS
    'Admin dashboard accounts, separate from app users. Created via backend/tools/create_admin.py.';

-- =============================================================================
-- TABLE: admin_audit_log
-- One row per admin action (logins and every write). target_id is not a
-- foreign key so entries outlive the user they describe; admin_id is SET NULL
-- if the admin account is removed.
-- =============================================================================
CREATE TABLE IF NOT EXISTS admin_audit_log (
    id                      BIGSERIAL       PRIMARY KEY,
    admin_id                BIGINT,
    action                  VARCHAR(64)     NOT NULL,
    target_type             VARCHAR(32),
    target_id               BIGINT,
    details                 JSONB           NOT NULL DEFAULT '{}'::jsonb,
    created_at              TIMESTAMPTZ     NOT NULL DEFAULT NOW(),

    CONSTRAINT fk_admin_audit_log_admin
        FOREIGN KEY (admin_id)
        REFERENCES admins (id)
        ON DELETE SET NULL
);

CREATE INDEX IF NOT EXISTS idx_admin_audit_log_created
    ON admin_audit_log (created_at DESC);

CREATE INDEX IF NOT EXISTS idx_admin_audit_log_target
    ON admin_audit_log (target_type, target_id, created_at DESC);

COMMENT ON TABLE admin_audit_log IS
    'Audit trail of admin dashboard actions (login, disable/enable/delete user, channel refresh, ...).';

-- Row-level security, as for every other table (see 01_app_backend.sql).
ALTER TABLE admins          ENABLE ROW LEVEL SECURITY;
ALTER TABLE admin_audit_log ENABLE ROW LEVEL SECURITY;
