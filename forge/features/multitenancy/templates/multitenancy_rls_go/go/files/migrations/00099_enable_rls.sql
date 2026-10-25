-- +goose Up
-- Row-Level Security on items for database.multitenancy=shared_rls: FORCE also
-- binds the table owner, and the policy compares customer_id with the
-- app.current_tenant setting the service binds per transaction with
-- set_config(..., true). NULLIF folds the never-set (NULL) and reset ('')
-- setting to NULL, so a connection without a tenant matches no rows instead of
-- failing the uuid cast. Project tables add their own policy in the migration
-- that creates them. Safe to re-run.
ALTER TABLE items ENABLE ROW LEVEL SECURITY;
ALTER TABLE items FORCE ROW LEVEL SECURITY;
DROP POLICY IF EXISTS tenant_isolation_items ON items;
CREATE POLICY tenant_isolation_items ON items
    USING (customer_id = NULLIF(current_setting('app.current_tenant', true), '')::uuid)
    WITH CHECK (customer_id = NULLIF(current_setting('app.current_tenant', true), '')::uuid);

-- +goose Down
DROP POLICY IF EXISTS tenant_isolation_items ON items;
ALTER TABLE items NO FORCE ROW LEVEL SECURITY;
ALTER TABLE items DISABLE ROW LEVEL SECURITY;
