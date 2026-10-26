package middleware

import (
	"context"
	"errors"
	"fmt"
	"log/slog"
	"net/url"
	"strings"
	"time"

	"github.com/redis/go-redis/v9"
)

// redisResponseCacheTimeout bounds dialing, reading, writing and waiting for
// a pooled connection unless the url sets them, so an unreachable Redis costs
// a request milliseconds instead of the client defaults' seconds of retries.
const redisResponseCacheTimeout = 250 * time.Millisecond

// RedisResponseCacheStore shares cached responses across replicas. The
// client connects lazily; an unreachable server makes lookups miss.
type RedisResponseCacheStore struct {
	client   *redis.Client
	redacted string
}

// NewRedisResponseCacheStore connects to the redis:// or rediss:// url and
// routes the client's own logs through slog.
func NewRedisResponseCacheStore(rawURL string) (*RedisResponseCacheStore, error) {
	parsed, err := url.Parse(rawURL)
	if err != nil {
		return nil, err
	}
	options, err := redis.ParseURL(rawURL)
	if err != nil {
		return nil, err
	}
	for _, timeout := range []*time.Duration{&options.DialTimeout, &options.ReadTimeout, &options.WriteTimeout, &options.PoolTimeout} {
		if *timeout == 0 {
			*timeout = redisResponseCacheTimeout
		}
	}
	if options.MaxRetries == 0 {
		options.MaxRetries = -1
	}
	options.DialerRetries = 1
	options.ContextTimeoutEnabled = true
	redis.SetLogger(redisLogger{})
	return &RedisResponseCacheStore{client: redis.NewClient(options), redacted: parsed.Redacted()}, nil
}

type redisLogger struct{}

func (redisLogger) Printf(ctx context.Context, format string, args ...any) {
	slog.WarnContext(ctx, fmt.Sprintf(format, args...), "component", "redis")
}

// String is the url with any password masked.
func (s *RedisResponseCacheStore) String() string { return s.redacted }

// Get reads the entry and its remaining ttl in one transaction.
func (s *RedisResponseCacheStore) Get(ctx context.Context, key string) ([]byte, time.Duration, bool, error) {
	var value *redis.StringCmd
	var ttl *redis.DurationCmd
	_, err := s.client.TxPipelined(ctx, func(pipe redis.Pipeliner) error {
		value = pipe.Get(ctx, key)
		ttl = pipe.PTTL(ctx, key)
		return nil
	})
	if errors.Is(err, redis.Nil) {
		return nil, 0, false, nil
	}
	if err != nil {
		return nil, 0, false, err
	}
	entry, _ := value.Bytes()
	return entry, max(ttl.Val(), 0), true, nil
}

// Set stores entry with ttl.
func (s *RedisResponseCacheStore) Set(ctx context.Context, key string, entry []byte, ttl time.Duration) error {
	return s.client.Set(ctx, key, entry, ttl).Err()
}

// Clear unlinks every key starting with prefix, scanning instead of KEYS so a
// large keyspace does not block the server.
func (s *RedisResponseCacheStore) Clear(ctx context.Context, prefix string) error {
	pattern := globEscaper.Replace(prefix) + "*"
	var cursor uint64
	for {
		keys, next, err := s.client.Scan(ctx, cursor, pattern, 500).Result()
		if err != nil {
			return err
		}
		if len(keys) > 0 {
			if err := s.client.Unlink(ctx, keys...).Err(); err != nil {
				return err
			}
		}
		if next == 0 {
			return nil
		}
		cursor = next
	}
}

// Close releases the connection pool.
func (s *RedisResponseCacheStore) Close() error {
	return s.client.Close()
}

var globEscaper = strings.NewReplacer(`\`, `\\`, `*`, `\*`, `?`, `\?`, `[`, `\[`, `]`, `\]`)
