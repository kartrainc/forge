package tenancy

import (
	"context"
	"fmt"
	"strings"

	"github.com/jackc/pgx/v5"
)

// RoleCheckSQL reports, for the session role, whether it is or can become a
// superuser or a BYPASSRLS role, and how many tables outside the system and
// temporary schemas it or a role it belongs to owns. Roles it is a member of
// count as its own: it inherits a table owner's rights (and the owner skips
// RLS) and can SET ROLE to a superuser or BYPASSRLS role.
const RoleCheckSQL = `
SELECT current_user,
       EXISTS (SELECT FROM pg_roles s
                WHERE s.rolsuper AND pg_has_role(current_user, s.oid, 'MEMBER')),
       EXISTS (SELECT FROM pg_roles b
                WHERE b.rolbypassrls AND pg_has_role(current_user, b.oid, 'MEMBER')),
       (SELECT count(*)
          FROM pg_class c
          JOIN pg_namespace n ON n.oid = c.relnamespace
         WHERE pg_has_role(current_user, c.relowner, 'MEMBER')
           AND c.relkind IN ('r', 'p')
           AND n.nspname NOT IN ('pg_catalog', 'information_schema')
           AND n.nspname NOT LIKE 'pg_toast%'
           AND n.nspname NOT LIKE 'pg_temp%')`

// RoleStatus is what RoleCheckSQL reports.
type RoleStatus struct {
	Name        string
	Superuser   bool
	BypassRLS   bool
	OwnedTables int64
}

// Problems lists why RLS would not hold for the role; empty when it does.
// Postgres skips RLS for superusers and BYPASSRLS roles even with FORCE ROW
// LEVEL SECURITY, and a table's owner can disable RLS or drop its policy.
func (s RoleStatus) Problems() []string {
	var found []string
	if s.Superuser {
		found = append(found, "is or belongs to a superuser")
	}
	if s.BypassRLS {
		found = append(found, "has or belongs to a role with BYPASSRLS")
	}
	if s.OwnedTables > 0 {
		found = append(found, fmt.Sprintf("owns or belongs to the owner of %d table(s)", s.OwnedTables))
	}
	return found
}

// Check returns an error when RLS would not isolate tenants for the role.
func (s RoleStatus) Check() error {
	problems := s.Problems()
	if len(problems) == 0 {
		return nil
	}
	return fmt.Errorf("database role %q %s, so Row-Level Security does not isolate tenants; "+
		"connect as a role that is NOSUPERUSER NOBYPASSRLS, owns no tables and is a member of no role that is or does "+
		"(database.rls_app_role provisions one), or turn off database.rls_startup_guard",
		s.Name, strings.Join(problems, ", "))
}

// Querier runs a single-row query; *pgxpool.Pool satisfies it.
type Querier interface {
	QueryRow(ctx context.Context, sql string, args ...any) pgx.Row
}

// AssertRLSEnforced checks the role db connects as and returns an error when
// it would bypass RLS (database.rls_startup_guard), so the service exits.
func AssertRLSEnforced(ctx context.Context, db Querier) error {
	var status RoleStatus
	if err := db.QueryRow(ctx, RoleCheckSQL).Scan(
		&status.Name, &status.Superuser, &status.BypassRLS, &status.OwnedTables,
	); err != nil {
		return fmt.Errorf("check the database role: %w", err)
	}
	return status.Check()
}
