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
# The owner needs CREATEROLE, not superuser: the role gets its attributes when
# it is created, and an existing role is only altered when one is unsafe
# (which takes a superuser, as it should). Postgres refuses NOSUPERUSER,
# NOBYPASSRLS or NOREPLICATION in ALTER ROLE from anyone else, even unchanged.
#
# Connection: DB_OWNER_URL (a postgresql:// URI) when set, else the standard
# PG* variables (PGHOST, PGUSER, PGPASSWORD, PGDATABASE). Role: APP_DB_ROLE and
# APP_DB_PASSWORD. The password never appears on a command line or in SQL text,
# which Postgres may log (log_statement=ddl, or a failed statement): it reaches
# the server as a session setting in the connection's startup options, and the
# server stores the SCRAM-SHA-256 verifier computed from it. It must be
# printable ASCII. Safe to re-run, and to run concurrently (every pod of a
# Deployment may run it): an advisory lock serializes the runs.
set -eu

: "${APP_DB_ROLE:?APP_DB_ROLE is required}"
: "${APP_DB_PASSWORD:?APP_DB_PASSWORD is required}"

# libpq splits startup options on spaces; a backslash escapes the next character.
escaped=$(printf '%s' "$APP_DB_PASSWORD" | sed 's/[\\ ]/\\&/g')
PGOPTIONS="${PGOPTIONS:-} -c forge.app_db_password=$escaped"
export PGOPTIONS

psql ${DB_OWNER_URL:+"$DB_OWNER_URL"} -v ON_ERROR_STOP=1 <<'SQL'
\getenv role APP_DB_ROLE
SELECT pg_advisory_lock(hashtext('forge:db-app-role'));
SELECT format(
    'CREATE ROLE %I LOGIN NOSUPERUSER NOBYPASSRLS NOCREATEDB NOCREATEROLE NOREPLICATION',
    :'role')
WHERE NOT EXISTS (SELECT FROM pg_roles WHERE rolname = :'role')\gexec
SELECT format('ALTER ROLE %I WITH %s', rolname, concat_ws(' ',
    CASE WHEN NOT rolcanlogin THEN 'LOGIN' END,
    CASE WHEN rolsuper THEN 'NOSUPERUSER' END,
    CASE WHEN rolbypassrls THEN 'NOBYPASSRLS' END,
    CASE WHEN rolcreatedb THEN 'NOCREATEDB' END,
    CASE WHEN rolcreaterole THEN 'NOCREATEROLE' END,
    CASE WHEN rolreplication THEN 'NOREPLICATION' END))
FROM pg_roles
WHERE rolname = :'role'
  AND (NOT rolcanlogin OR rolsuper OR rolbypassrls OR rolcreatedb OR rolcreaterole
       OR rolreplication)\gexec
SELECT set_config('forge.app_db_role', :'role', false);
-- The SCRAM-SHA-256 verifier of the password (RFC 5802, 7677), as Postgres
-- stores it: PBKDF2-HMAC-SHA-256 with a random salt and 4096 iterations.
CREATE FUNCTION pg_temp.forge_hmac(key bytea, msg bytea) RETURNS bytea
LANGUAGE plpgsql IMMUTABLE AS $f$
DECLARE
    k bytea := CASE WHEN length(key) > 64 THEN sha256(key) ELSE key END;
    inner_key bytea;
    outer_key bytea;
BEGIN
    k := k || decode(repeat('00', 64 - length(k)), 'hex');
    inner_key := k;
    outer_key := k;
    FOR i IN 0..63 LOOP
        inner_key := set_byte(inner_key, i, get_byte(k, i) # 54);
        outer_key := set_byte(outer_key, i, get_byte(k, i) # 92);
    END LOOP;
    RETURN sha256(outer_key || sha256(inner_key || msg));
END
$f$;
DO $do$
DECLARE
    password text := current_setting('forge.app_db_password', true);
    iterations constant int := 4096;
    salt bytea := uuid_send(gen_random_uuid());
    u bytea;
    salted bytea;
BEGIN
    IF password IS NULL OR password = '' THEN
        RAISE EXCEPTION 'forge.app_db_password is not set: DB_OWNER_URL must not override options';
    END IF;
    IF password !~ '^[ -~]+$' THEN
        RAISE EXCEPTION 'APP_DB_PASSWORD must be printable ASCII';
    END IF;
    u := pg_temp.forge_hmac(convert_to(password, 'UTF8'), salt || '\x00000001'::bytea);
    salted := u;
    FOR n IN 2..iterations LOOP
        u := pg_temp.forge_hmac(convert_to(password, 'UTF8'), u);
        FOR i IN 0..31 LOOP
            salted := set_byte(salted, i, get_byte(salted, i) # get_byte(u, i));
        END LOOP;
    END LOOP;
    -- A failure's CONTEXT would carry the statement, verifier included, to the
    -- log and stderr: re-raise it without. (On Postgres 16+ a CREATEROLE owner
    -- may alter only a role it created or holds ADMIN OPTION on.)
    BEGIN
        EXECUTE format('ALTER ROLE %I PASSWORD %L', current_setting('forge.app_db_role'), format(
            'SCRAM-SHA-256$%s:%s$%s:%s',
            iterations,
            encode(salt, 'base64'),
            encode(sha256(pg_temp.forge_hmac(salted, convert_to('Client Key', 'UTF8'))), 'base64'),
            encode(pg_temp.forge_hmac(salted, convert_to('Server Key', 'UTF8')), 'base64')));
    EXCEPTION
        WHEN insufficient_privilege THEN
            RAISE EXCEPTION 'cannot set the password of role %: %',
                current_setting('forge.app_db_role'), SQLERRM
                USING ERRCODE = SQLSTATE,
                      HINT = 'The role exists and this owner did not create it: grant the owner '
                          || 'ADMIN OPTION on it, or set database.rls_app_role=false.';
        WHEN OTHERS THEN
            RAISE EXCEPTION 'cannot set the password of role %: %',
                current_setting('forge.app_db_role'), SQLERRM
                USING ERRCODE = SQLSTATE;
    END;
END
$do$;
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
