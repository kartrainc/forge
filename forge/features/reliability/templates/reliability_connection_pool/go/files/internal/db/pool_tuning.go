package db

import (
	"fmt"
	"strconv"
	"time"

	"github.com/jackc/pgx/v5/pgxpool"
)

// Pool sizing defaults; each is overridable through its PGX_* variable.
const (
	DefaultPoolMaxConns       = 20               // PGX_MAX_CONNECTIONS
	DefaultPoolMinConns       = 2                // PGX_MIN_CONNECTIONS
	DefaultPoolConnectTimeout = 10 * time.Second // PGX_CONNECT_TIMEOUT_SECS
	DefaultPoolIdleTimeout    = 10 * time.Minute // PGX_IDLE_TIMEOUT_SECS
)

// TunePool applies production pool sizing to cfg. The PGX_* variables take
// precedence over db.pool_max; a connect_timeout in the connection string is
// kept unless PGX_CONNECT_TIMEOUT_SECS is set. Invalid values are errors.
func TunePool(cfg *pgxpool.Config, getenv func(string) string) error {
	maxConns, err := envCount(getenv, "PGX_MAX_CONNECTIONS", DefaultPoolMaxConns, 1)
	if err != nil {
		return err
	}
	minConns, err := envCount(getenv, "PGX_MIN_CONNECTIONS", DefaultPoolMinConns, 0)
	if err != nil {
		return err
	}
	if minConns > maxConns {
		return fmt.Errorf("PGX_MIN_CONNECTIONS (%d) exceeds PGX_MAX_CONNECTIONS (%d)", minConns, maxConns)
	}
	idle, err := envSeconds(getenv, "PGX_IDLE_TIMEOUT_SECS", DefaultPoolIdleTimeout)
	if err != nil {
		return err
	}
	connect, err := envSeconds(getenv, "PGX_CONNECT_TIMEOUT_SECS", DefaultPoolConnectTimeout)
	if err != nil {
		return err
	}
	if getenv("PGX_CONNECT_TIMEOUT_SECS") != "" || cfg.ConnConfig.ConnectTimeout == 0 {
		cfg.ConnConfig.ConnectTimeout = connect
	}
	cfg.MaxConns, cfg.MinConns, cfg.MaxConnIdleTime = maxConns, minConns, idle
	return nil
}

func envCount(getenv func(string) string, name string, fallback, floor int32) (int32, error) {
	raw := getenv(name)
	if raw == "" {
		return fallback, nil
	}
	value, err := strconv.ParseInt(raw, 10, 32)
	if err != nil || int32(value) < floor {
		return 0, fmt.Errorf("invalid %s %q: must be an integer >= %d", name, raw, floor)
	}
	return int32(value), nil
}

func envSeconds(getenv func(string) string, name string, fallback time.Duration) (time.Duration, error) {
	raw := getenv(name)
	if raw == "" {
		return fallback, nil
	}
	seconds, err := strconv.Atoi(raw)
	if err != nil || seconds < 1 {
		return 0, fmt.Errorf("invalid %s %q: must be a whole number of seconds >= 1", name, raw)
	}
	return time.Duration(seconds) * time.Second, nil
}
