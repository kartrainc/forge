// Package config implements the RFC-008 layered configuration loader.
//
// Priority (highest wins):
//  1. Env vars prefixed APP__ (nested keys via __, e.g. APP__SERVER__PORT=8080).
//  2. .secrets.yaml (gitignored; for local dev secrets).
//  3. config/<ENV>.yaml (selected by ENV, then APP_ENV, default development).
//  4. config/defaults.yaml.
//  5. Struct defaults.
package config

import (
	"errors"
	"fmt"
	"os"
	"path/filepath"
	"strings"

	"gopkg.in/yaml.v3"
)

type AppInfo struct {
	Name    string `yaml:"name"`
	Version string `yaml:"version"`
	Env     string `yaml:"env"`
}

type CORSConfig struct {
	Enabled          bool     `yaml:"enabled"`
	AllowOrigins     []string `yaml:"allow_origins"`
	AllowCredentials bool     `yaml:"allow_credentials"`
	MaxAge           int      `yaml:"max_age"`
}

type ServerConfig struct {
	Host string     `yaml:"host"`
	Port int        `yaml:"port"`
	CORS CORSConfig `yaml:"cors"`
}

type DBConfig struct {
	URL                string `yaml:"url"`
	PoolMin            int32  `yaml:"pool_min"`
	PoolMax            int32  `yaml:"pool_max"`
	StatementTimeoutMS int    `yaml:"statement_timeout_ms"`
}

type LoggingConfig struct {
	Level  string `yaml:"level"`
	Pretty bool   `yaml:"pretty"`
}

type AuthConfig struct {
	Enabled   bool   `yaml:"enabled"`
	ServerURL string `yaml:"server_url"`
	Realm     string `yaml:"realm"`
	ClientID  string `yaml:"client_id"`
	Audience  string `yaml:"audience"`
	JWKSURI   string `yaml:"jwks_uri"`
}

type SecurityConfig struct {
	Auth AuthConfig `yaml:"auth"`
}

type Config struct {
	App      AppInfo        `yaml:"app"`
	Server   ServerConfig   `yaml:"server"`
	DB       DBConfig       `yaml:"db"`
	Logging  LoggingConfig  `yaml:"logging"`
	Security SecurityConfig `yaml:"security"`
}

// LoadOptions overrides the project root and profile; zero values read the
// working directory and the ENV / APP_ENV variables.
type LoadOptions struct {
	ProjectRoot string
	Env         string
}

var listKeys = map[string]bool{"server.cors.allow_origins": true}

// Defaults returns the struct defaults that apply when no file sets a key.
func Defaults() Config {
	url := os.Getenv("DATABASE_URL")
	if url == "" {
		url = "postgresql://localhost:5432/app"
	}
	return Config{
		App: AppInfo{Name: "service", Version: "0.0.0", Env: "development"},
		Server: ServerConfig{
			Host: "0.0.0.0",
			Port: 5000,
			CORS: CORSConfig{Enabled: true, AllowOrigins: []string{"*"}, MaxAge: 600},
		},
		DB:      DBConfig{URL: url, PoolMin: 2, PoolMax: 10, StatementTimeoutMS: 30000},
		Logging: LoggingConfig{Level: "info"},
	}
}

// Load reads the layered configuration and validates it.
func Load(options LoadOptions) (*Config, error) {
	root := options.ProjectRoot
	if root == "" {
		wd, err := os.Getwd()
		if err != nil {
			return nil, err
		}
		root = wd
	}
	env := firstNonEmpty(options.Env, os.Getenv("ENV"), os.Getenv("APP_ENV"), "development")

	merged := map[string]any{}
	for _, path := range []string{
		filepath.Join(root, "config", "defaults.yaml"),
		filepath.Join(root, "config", env+".yaml"),
		filepath.Join(root, ".secrets.yaml"),
	} {
		layer, err := readYAML(path)
		if err != nil {
			return nil, err
		}
		deepMerge(merged, layer)
	}
	applyEnv(merged, os.Environ())
	setPath(merged, []string{"app", "env"}, env)

	cfg := Defaults()
	encoded, err := yaml.Marshal(merged)
	if err != nil {
		return nil, err
	}
	if err := yaml.Unmarshal(encoded, &cfg); err != nil {
		return nil, fmt.Errorf("invalid configuration: %w", err)
	}
	// The guard treats an unset env as production (fail closed) rather than
	// the development profile default.
	guardEnv := firstNonEmpty(options.Env, os.Getenv("ENV"), os.Getenv("APP_ENV"), "production")
	if err := cfg.Validate(guardEnv, os.Getenv); err != nil {
		return nil, err
	}
	return &cfg, nil
}

// Validate fails closed in production-like environments when auth is enabled
// but the Gatekeeper issuer or the service audience is unset.
func (c *Config) Validate(env string, getenv func(string) string) error {
	if c.DB.PoolMax < c.DB.PoolMin {
		return errors.New("db.pool_max must be greater than or equal to db.pool_min")
	}
	switch strings.ToLower(strings.TrimSpace(env)) {
	case "development", "dev", "local", "test", "testing":
		return nil
	}
	if !c.Security.Auth.Enabled {
		return nil
	}
	for _, name := range []string{"GATEKEEPER_ISSUER", "SERVICE_AUDIENCE"} {
		if strings.TrimSpace(getenv(name)) == "" {
			return fmt.Errorf("%s is unset or blank but security.auth.enabled is true — "+
				"set the real Gatekeeper issuer/audience before running in production", name)
		}
	}
	return nil
}

func readYAML(path string) (map[string]any, error) {
	data, err := os.ReadFile(path)
	if errors.Is(err, os.ErrNotExist) {
		return map[string]any{}, nil
	}
	if err != nil {
		return nil, err
	}
	layer := map[string]any{}
	if err := yaml.Unmarshal(data, &layer); err != nil {
		return nil, fmt.Errorf("%s: %w", path, err)
	}
	return layer, nil
}

func deepMerge(dst, src map[string]any) {
	for key, value := range src {
		nested, isMap := value.(map[string]any)
		existing, hasMap := dst[key].(map[string]any)
		if isMap && hasMap {
			deepMerge(existing, nested)
			continue
		}
		dst[key] = value
	}
}

func applyEnv(merged map[string]any, environ []string) {
	for _, entry := range environ {
		name, raw, found := strings.Cut(entry, "=")
		if !found || !strings.HasPrefix(name, "APP__") {
			continue
		}
		parts := strings.Split(strings.ToLower(strings.TrimPrefix(name, "APP__")), "__")
		var value any
		if listKeys[strings.Join(parts, ".")] {
			items := []any{}
			for _, item := range strings.Split(raw, ",") {
				if item = strings.TrimSpace(item); item != "" {
					items = append(items, item)
				}
			}
			value = items
		} else {
			value = scalar(raw)
		}
		setPath(merged, parts, value)
	}
}

// scalar parses booleans and numbers; everything else stays a string.
func scalar(raw string) any {
	var value any
	if err := yaml.Unmarshal([]byte(raw), &value); err == nil {
		switch value.(type) {
		case bool, int, float64:
			return value
		}
	}
	return raw
}

func setPath(target map[string]any, parts []string, value any) {
	for _, part := range parts[:len(parts)-1] {
		next, ok := target[part].(map[string]any)
		if !ok {
			next = map[string]any{}
			target[part] = next
		}
		target = next
	}
	target[parts[len(parts)-1]] = value
}

func firstNonEmpty(values ...string) string {
	for _, value := range values {
		if value != "" {
			return value
		}
	}
	return ""
}
