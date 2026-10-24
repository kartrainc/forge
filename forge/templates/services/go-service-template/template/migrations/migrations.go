// Package migrations embeds the SQL migrations and applies them with goose.
package migrations

import (
	"context"
	"database/sql"
	"embed"
	"fmt"

	_ "github.com/jackc/pgx/v5/stdlib" // registers the "pgx" database/sql driver
	"github.com/pressly/goose/v3"
	"github.com/pressly/goose/v3/lock"
)

//go:embed *.sql
var files embed.FS

// Up applies every pending migration under a Postgres session lock, so
// concurrent replicas serialise instead of racing.
func Up(ctx context.Context, url string) error {
	db, err := sql.Open("pgx", url)
	if err != nil {
		return err
	}
	defer db.Close()
	return UpDB(ctx, db)
}

// UpDB applies the migrations through an existing connection pool.
func UpDB(ctx context.Context, db *sql.DB) error {
	locker, err := lock.NewPostgresSessionLocker()
	if err != nil {
		return err
	}
	provider, err := goose.NewProvider(goose.DialectPostgres, db, files, goose.WithSessionLocker(locker))
	if err != nil {
		return err
	}
	if _, err := provider.Up(ctx); err != nil {
		return fmt.Errorf("apply migrations: %w", err)
	}
	return nil
}
