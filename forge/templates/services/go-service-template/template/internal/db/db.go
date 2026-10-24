// Package db opens the PostgreSQL pool.
package db

import (
	"context"
	"fmt"
	"os"
	"time"

	"github.com/jackc/pgx/v5/pgxpool"
)

// URL prefers DATABASE_URL over the configured url.
func URL(configured string) string {
	if url := os.Getenv("DATABASE_URL"); url != "" {
		return url
	}
	return configured
}

// NewPool validates the url without connecting. The first query connects, so
// an unreachable database at startup does not crash the process; readiness
// reports it as 503 until Postgres is reachable.
func NewPool(ctx context.Context, url string, maxConns int32, statementTimeout time.Duration) (*pgxpool.Pool, error) {
	cfg, err := pgxpool.ParseConfig(url)
	if err != nil {
		return nil, fmt.Errorf("invalid database url: %w", err)
	}
	if maxConns > 0 {
		cfg.MaxConns = maxConns
	}
	cfg.MinConns = 0
	if statementTimeout > 0 {
		cfg.ConnConfig.RuntimeParams["statement_timeout"] = fmt.Sprint(statementTimeout.Milliseconds())
	}
	// FORGE:DB_POOL_CONFIG
	return pgxpool.NewWithConfig(ctx, cfg)
}
