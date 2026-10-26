// Package migrations embeds the SQL migrations and runs them with goose.
//
// Migrations numbered below 00100 come from forge; project migrations start at
// 00100. Out-of-order migrations are allowed, so one that a forge update adds
// below the database's latest version still applies. Every applied version
// must keep its file: down cannot roll back while one is missing.
package migrations

import (
	"context"
	"database/sql"
	"embed"
	"errors"
	"fmt"
	"io"
	"io/fs"
	"log/slog"
	"maps"
	"path"
	"slices"
	"strconv"
	"strings"
	"text/tabwriter"
	"time"

	"github.com/jackc/pgx/v5"
	"github.com/jackc/pgx/v5/stdlib"
	"github.com/pressly/goose/v3"
	"github.com/pressly/goose/v3/lock"
)

//go:embed *.sql
var files embed.FS

// Usage is the migrate command line; without a command it runs up.
const Usage = "migrate [up|down|status|version]"

// RLSVersion is forge's row-level security migration
// (database.multitenancy=shared_rls).
const RLSVersion = 99

var commands = []string{"up", "down", "status", "version"}

// ErrUsage reports a command line that names no known command.
var ErrUsage = errors.New("usage: " + Usage)

// Migrator is a goose provider plus the pool it runs on, so status and
// version can read the version table without the session lock.
type Migrator struct {
	*goose.Provider
	db *sql.DB
}

// Embedded returns the SQL migrations compiled into the binary.
func Embedded() fs.FS {
	return files
}

// NewProvider returns a goose provider for fsys that serialises runs under a
// Postgres session lock, so concurrent replicas wait instead of racing, and
// applies missing lower-numbered migrations.
func NewProvider(db *sql.DB, fsys fs.FS) (*goose.Provider, error) {
	locker, err := lock.NewPostgresSessionLocker()
	if err != nil {
		return nil, err
	}
	return goose.NewProvider(goose.DialectPostgres, db, fsys,
		goose.WithSessionLocker(locker), goose.WithAllowOutofOrder(true))
}

// Open returns a Migrator for fsys on a new connection pool to url; closing
// it closes the pool.
func Open(url string, fsys fs.FS) (*Migrator, error) {
	config, err := pgx.ParseConfig(url)
	if err != nil {
		return nil, fmt.Errorf("invalid database url: %w", err)
	}
	db := stdlib.OpenDB(*config)
	provider, err := NewProvider(db, fsys)
	if err != nil {
		_ = db.Close()
		return nil, err
	}
	return &Migrator{Provider: provider, db: db}, nil
}

// Up applies every pending embedded migration.
func Up(ctx context.Context, url string) error {
	migrator, err := Open(url, files)
	if err != nil {
		return err
	}
	defer migrator.Close()
	_, err = migrator.Up(ctx)
	return wrapUp(err)
}

// UpDB applies every pending embedded migration through an existing pool,
// which stays open.
func UpDB(ctx context.Context, db *sql.DB) error {
	provider, err := NewProvider(db, files)
	if err != nil {
		return err
	}
	_, err = provider.Up(ctx)
	return wrapUp(err)
}

func wrapUp(err error) error {
	if err != nil {
		return fmt.Errorf("apply migrations: %w", err)
	}
	return nil
}

// ParseCommand returns the command named by args; no argument means up.
func ParseCommand(args []string) (string, error) {
	if len(args) == 0 {
		return "up", nil
	}
	if len(args) > 1 || !slices.Contains(commands, args[0]) {
		return "", fmt.Errorf("%w (got %q)", ErrUsage, args)
	}
	return args[0], nil
}

// Run executes command with m: up applies every pending migration, down rolls
// back the most recently applied one, status writes each migration's state to
// out and version writes the highest applied version. status and version only
// read the database.
func Run(ctx context.Context, m *Migrator, command string, out io.Writer) error {
	switch command {
	case "up":
		results, err := m.Up(ctx)
		var partial *goose.PartialError
		if errors.As(err, &partial) {
			results = partial.Applied
		}
		for _, result := range results {
			logResult(ctx, "migration applied", result)
		}
		if err != nil {
			return wrapUp(err)
		}
		slog.InfoContext(ctx, "migrations applied successfully", "applied", len(results))
		return nil
	case "down":
		return down(ctx, m)
	case "status":
		applied, err := m.applied(ctx)
		if err != nil {
			return fmt.Errorf("read migration status: %w", err)
		}
		return writeStatus(out, m.ListSources(), applied)
	case "version":
		applied, err := m.applied(ctx)
		if err != nil {
			return fmt.Errorf("read database version: %w", err)
		}
		var latest int64
		for version := range applied {
			latest = max(latest, version)
		}
		_, err = fmt.Fprintln(out, latest)
		return err
	}
	return fmt.Errorf("%w (got %q)", ErrUsage, command)
}

func down(ctx context.Context, m *Migrator) error {
	result, err := m.Down(ctx)
	if errors.Is(err, goose.ErrNoNextVersion) {
		return errors.New("roll back migration: no applied migration")
	}
	if errors.Is(err, goose.ErrVersionNotFound) {
		if orphans := m.orphans(ctx); len(orphans) > 0 {
			return fmt.Errorf("roll back migration: no migration file for applied versions %s: %w",
				strings.Join(orphans, ", "), err)
		}
	}
	if err != nil {
		return fmt.Errorf("roll back migration: %w", err)
	}
	logResult(ctx, "migration rolled back", result)
	if result.Source.Version == RLSVersion {
		slog.WarnContext(ctx, "row-level security rolled back: the database no longer isolates tenants; run migrate up to restore it",
			"version", result.Source.Version)
	}
	return nil
}

// applied returns when each recorded version was applied. It neither takes
// the session lock nor creates the version table: a database without one has
// nothing applied.
func (m *Migrator) applied(ctx context.Context) (map[int64]time.Time, error) {
	tx, err := m.db.BeginTx(ctx, &sql.TxOptions{ReadOnly: true})
	if err != nil {
		return nil, err
	}
	defer func() { _ = tx.Rollback() }()
	var exists bool
	err = tx.QueryRowContext(ctx, "SELECT to_regclass('"+goose.DefaultTablename+"') IS NOT NULL").Scan(&exists)
	if err != nil {
		return nil, err
	}
	applied := map[int64]time.Time{}
	if !exists {
		return applied, nil
	}
	rows, err := tx.QueryContext(ctx,
		"SELECT version_id, max(tstamp) FROM "+goose.DefaultTablename+" WHERE version_id > 0 GROUP BY version_id")
	if err != nil {
		return nil, err
	}
	defer rows.Close()
	for rows.Next() {
		var version int64
		var at time.Time
		if err := rows.Scan(&version, &at); err != nil {
			return nil, err
		}
		applied[version] = at
	}
	return applied, rows.Err()
}

// orphans lists the applied versions that have no migration file.
func (m *Migrator) orphans(ctx context.Context) []string {
	applied, err := m.applied(ctx)
	if err != nil {
		return nil
	}
	for _, source := range m.ListSources() {
		delete(applied, source.Version)
	}
	versions := slices.Sorted(maps.Keys(applied))
	names := make([]string, 0, len(versions))
	for _, version := range versions {
		names = append(names, strconv.FormatInt(version, 10))
	}
	return names
}

func writeStatus(out io.Writer, sources []*goose.Source, applied map[int64]time.Time) error {
	names := map[int64]string{}
	for _, source := range sources {
		names[source.Version] = path.Base(source.Path)
	}
	versions := slices.Collect(maps.Keys(names))
	for version := range applied {
		if _, ok := names[version]; !ok {
			versions = append(versions, version)
			names[version] = "(no file)"
		}
	}
	slices.Sort(versions)
	table := tabwriter.NewWriter(out, 0, 0, 2, ' ', 0)
	fmt.Fprintln(table, "VERSION\tSTATE\tAPPLIED AT\tMIGRATION")
	for _, version := range versions {
		state, appliedAt := "pending", "-"
		if at, ok := applied[version]; ok {
			state, appliedAt = "applied", at.UTC().Format(time.RFC3339)
		}
		fmt.Fprintf(table, "%d\t%s\t%s\t%s\n", version, state, appliedAt, names[version])
	}
	return table.Flush()
}

func logResult(ctx context.Context, message string, result *goose.MigrationResult) {
	slog.InfoContext(ctx, message,
		"version", result.Source.Version,
		"migration", path.Base(result.Source.Path),
		"duration_ms", result.Duration.Milliseconds())
}
