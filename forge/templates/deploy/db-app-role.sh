#!/bin/sh
# Create or update the role a shared_rls service runs as (database.rls_app_role).
#
# Postgres skips Row-Level Security for superusers and BYPASSRLS roles, so the
# service must not connect as the role that owns its tables. This script runs
# as that owner after every migration and leaves APP_DB_ROLE with:
#   LOGIN NOSUPERUSER NOBYPASSRLS, no CREATE on public,
#   SELECT/INSERT/UPDATE/DELETE on the tables and USAGE on the sequences of
#   public, now and for tables the owner creates later.
#
# Connection: DB_OWNER_URL (a postgresql:// URI) when set, else the standard
# PG* variables (PGHOST, PGUSER, PGPASSWORD, PGDATABASE). Role: APP_DB_ROLE and
# APP_DB_PASSWORD, which psql reads from the environment (\getenv, psql 15+),
# so the password never appears on a command line. Safe to re-run, and to run
# concurrently (every pod of a Deployment may run it): an advisory lock
# serializes the runs.
set -eu

: "${APP_DB_ROLE:?APP_DB_ROLE is required}"
: "${APP_DB_PASSWORD:?APP_DB_PASSWORD is required}"

psql ${DB_OWNER_URL:+"$DB_OWNER_URL"} -v ON_ERROR_STOP=1 <<'SQL'
\getenv role APP_DB_ROLE
\getenv password APP_DB_PASSWORD
SELECT pg_advisory_lock(hashtext('forge:db-app-role'));
SELECT format('CREATE ROLE %I LOGIN', :'role')
WHERE NOT EXISTS (SELECT FROM pg_roles WHERE rolname = :'role')\gexec
SELECT format(
    'ALTER ROLE %I WITH LOGIN NOSUPERUSER NOBYPASSRLS NOCREATEDB NOCREATEROLE NOREPLICATION PASSWORD %L',
    :'role', :'password')\gexec
SELECT format('GRANT CONNECT ON DATABASE %I TO %I', current_database(), :'role')\gexec
SELECT format('GRANT USAGE ON SCHEMA public TO %I', :'role')\gexec
SELECT format('REVOKE CREATE ON SCHEMA public FROM %I', :'role')\gexec
SELECT format('GRANT SELECT, INSERT, UPDATE, DELETE ON ALL TABLES IN SCHEMA public TO %I', :'role')\gexec
SELECT format('GRANT USAGE, SELECT ON ALL SEQUENCES IN SCHEMA public TO %I', :'role')\gexec
SELECT format(
    'ALTER DEFAULT PRIVILEGES IN SCHEMA public GRANT SELECT, INSERT, UPDATE, DELETE ON TABLES TO %I',
    :'role')\gexec
SELECT format(
    'ALTER DEFAULT PRIVILEGES IN SCHEMA public GRANT USAGE, SELECT ON SEQUENCES TO %I',
    :'role')\gexec
SQL
