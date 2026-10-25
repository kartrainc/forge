// Package pii scrubs emails, credentials, bearer tokens, JWTs, API keys and
// secret values from log records. It uses the Python service's
// PiiRedactionFilter placeholders and covers more shapes.
package pii

import (
	"bytes"
	"context"
	"encoding/json"
	"fmt"
	"log/slog"
	"reflect"
	"regexp"
	"slices"
)

var (
	urlCredentialPattern = regexp.MustCompile(`(?i)\b([a-z][a-z0-9+.\-]*://[^\s:/@]*:)[^\s/]+@`)
	emailPattern         = regexp.MustCompile(`(?i)\b[a-z0-9._%+\-]+@[a-z0-9.\-]+\.[a-z]{2,}\b`)
	bearerPattern        = regexp.MustCompile(`(?i)\bbearer\s+[a-z0-9._\-]{16,}`)
	authorizationPattern = regexp.MustCompile(`(?i)(\bauthorization["']?\s*[:=]\s*["']?(?:(?:basic|bearer|digest|negotiate|token)\s+)?)[^\s'",;}]+`)
	jwtPattern           = regexp.MustCompile(`\beyJ[A-Za-z0-9_-]+\.[A-Za-z0-9_-]+\.[A-Za-z0-9_-]*`)
	apiKeyPattern        = regexp.MustCompile(`\b(?:sk-(?:[A-Za-z0-9]+[-_])*[A-Za-z0-9]{16,}[A-Za-z0-9_-]*|AIza[A-Za-z0-9_-]{20,}|hf_[A-Za-z0-9]{16,})`)
)

// secretKeyPrefix matches a secret-named key and its separator; the patterns
// below redact the whole value after it, quoted values to their closing quote.
const secretKeyPrefix = `(?i)((?:^|[^a-z0-9_.\-])["']?[a-z0-9_.\-]*?(?:password|passwd|passphrase|secret|token|credentials?|(?:api|access|private|secret)[_-]?key)["']?\s*[:=]\s*)`

var secretPairPatterns = []struct {
	pattern     *regexp.Regexp
	replacement string
}{
	{regexp.MustCompile(secretKeyPrefix + `"(?:[^"\\]|\\.)*"`), `${1}"[redacted]"`},
	{regexp.MustCompile(secretKeyPrefix + `'(?:[^'\\]|\\.)*'`), `${1}'[redacted]'`},
	{regexp.MustCompile(secretKeyPrefix + `"[^"]*$`), `${1}"[redacted]`},
	{regexp.MustCompile(secretKeyPrefix + `'[^']*$`), `${1}'[redacted]`},
	{regexp.MustCompile(secretKeyPrefix + `[^\s'",;}&]+`), `${1}[redacted]`},
}

// candidates are the lower-case fragments every pattern above needs; text
// without any of them is returned as is.
var candidates = []string{
	"@", "bearer", "authorization", "eyj", "sk-", "aiza", "hf_", "pass", "secret", "token", "key", "credential",
}

// secretKeys are attribute key suffixes, lower-case without _ - and ., whose
// values are always redacted.
var secretKeys = []string{
	"password", "passwd", "passphrase", "secret", "token", "apikey", "secretkey", "privatekey", "accesskey",
	"authorization", "cookie", "credential", "credentials",
}

var candidateStart = func() (start [256]bool) {
	for _, candidate := range candidates {
		start[candidate[0]] = true
		start[upper(candidate[0])] = true
	}
	return start
}()

// Redact replaces every PII or secret shape in text with its placeholder.
func Redact(text string) string {
	if !hasCandidate(text) {
		return text
	}
	text = urlCredentialPattern.ReplaceAllString(text, "${1}[redacted]@")
	text = emailPattern.ReplaceAllLiteralString(text, "[email]")
	text = bearerPattern.ReplaceAllLiteralString(text, "bearer [redacted]")
	text = authorizationPattern.ReplaceAllString(text, "${1}[redacted]")
	text = jwtPattern.ReplaceAllLiteralString(text, "[jwt]")
	text = apiKeyPattern.ReplaceAllLiteralString(text, "[api-key]")
	for _, secret := range secretPairPatterns {
		text = secret.pattern.ReplaceAllString(text, secret.replacement)
	}
	return text
}

func hasCandidate(text string) bool {
	for i := range len(text) {
		if !candidateStart[text[i]] {
			continue
		}
		for _, candidate := range candidates {
			if hasPrefixFold(text[i:], candidate) {
				return true
			}
		}
	}
	return false
}

func hasPrefixFold(text, prefix string) bool {
	if len(text) < len(prefix) {
		return false
	}
	for i := range len(prefix) {
		if lower(text[i]) != prefix[i] {
			return false
		}
	}
	return true
}

// secretKey reports whether key, ignoring case and the separators _ - and .,
// ends with one of secretKeys.
func secretKey(key string) bool {
	for _, suffix := range secretKeys {
		if hasNormalizedSuffix(key, suffix) {
			return true
		}
	}
	return false
}

func hasNormalizedSuffix(key, suffix string) bool {
	i := len(key)
	for j := len(suffix) - 1; j >= 0; j-- {
		for i > 0 && (key[i-1] == '_' || key[i-1] == '-' || key[i-1] == '.') {
			i--
		}
		if i == 0 || lower(key[i-1]) != suffix[j] {
			return false
		}
		i--
	}
	return true
}

func lower(c byte) byte {
	if 'A' <= c && c <= 'Z' {
		return c + 'a' - 'A'
	}
	return c
}

func upper(c byte) byte {
	if 'a' <= c && c <= 'z' {
		return c - 'a' + 'A'
	}
	return c
}

// Handler redacts the message and attributes of every record before the
// wrapped handler sees them. Records without anything to redact pass through
// unchanged.
type Handler struct {
	inner slog.Handler
}

// NewHandler wraps inner; a handler that already redacts, directly or
// through handlers wrapping a Handler, is returned unchanged.
func NewHandler(inner slog.Handler) slog.Handler {
	if Redacts(inner) {
		return inner
	}
	return &Handler{inner: inner}
}

// probeLevel is a level no logger uses; Redacts asks a handler chain whether
// it is enabled at it.
const probeLevel = slog.Level(-1 << 20)

type probeKey struct{}

// Redacts reports whether handler is a Handler or wraps one.
func Redacts(handler slog.Handler) bool {
	found := false
	handler.Enabled(context.WithValue(context.Background(), probeKey{}, &found), probeLevel)
	return found
}

// Enabled defers to the wrapped handler.
func (h *Handler) Enabled(ctx context.Context, level slog.Level) bool {
	if level == probeLevel {
		if found, ok := ctx.Value(probeKey{}).(*bool); ok {
			*found = true
			return false
		}
	}
	return h.inner.Enabled(ctx, level)
}

// Handle passes a redacted copy of record to the wrapped handler.
func (h *Handler) Handle(ctx context.Context, record slog.Record) error {
	message := Redact(record.Message)
	changed := message != record.Message
	var buffer [8]slog.Attr
	attrs := buffer[:0]
	record.Attrs(func(attr slog.Attr) bool {
		redacted, ok := redactAttr(attr)
		changed = changed || ok
		attrs = append(attrs, redacted)
		return true
	})
	if !changed {
		return h.inner.Handle(ctx, record)
	}
	redacted := slog.NewRecord(record.Time, record.Level, message, record.PC)
	redacted.AddAttrs(attrs...)
	return h.inner.Handle(ctx, redacted)
}

// WithAttrs redacts attrs before binding them to the wrapped handler.
func (h *Handler) WithAttrs(attrs []slog.Attr) slog.Handler {
	if redacted, changed := redactAttrs(attrs); changed {
		attrs = redacted
	}
	return &Handler{inner: h.inner.WithAttrs(attrs)}
}

// WithGroup opens a group on the wrapped handler.
func (h *Handler) WithGroup(name string) slog.Handler {
	return &Handler{inner: h.inner.WithGroup(name)}
}

func redactAttrs(attrs []slog.Attr) ([]slog.Attr, bool) {
	var redacted []slog.Attr
	for i, attr := range attrs {
		if next, changed := redactAttr(attr); changed {
			if redacted == nil {
				redacted = slices.Clone(attrs)
			}
			redacted[i] = next
		}
	}
	return redacted, redacted != nil
}

// redactAttr masks the whole value of a secret key, whatever its kind, and
// otherwise redacts strings, groups, errors, Stringers, byte slices and
// JSON-encodable values. It reports whether attr changed.
func redactAttr(attr slog.Attr) (slog.Attr, bool) {
	if secretKey(attr.Key) {
		return slog.String(attr.Key, "[redacted]"), true
	}
	value := attr.Value.Resolve()
	switch value.Kind() {
	case slog.KindString:
		text := value.String()
		if redacted := Redact(text); redacted != text {
			return slog.String(attr.Key, redacted), true
		}
	case slog.KindGroup:
		if group, changed := redactAttrs(value.Group()); changed {
			return slog.Attr{Key: attr.Key, Value: slog.GroupValue(group...)}, true
		}
	case slog.KindAny:
		if redacted, changed := redactAny(value.Any()); changed {
			return slog.Attr{Key: attr.Key, Value: redacted}, true
		}
	}
	if attr.Value.Kind() == slog.KindLogValuer {
		return slog.Attr{Key: attr.Key, Value: value}, true
	}
	return attr, false
}

func redactAny(value any) (slog.Value, bool) {
	switch typed := value.(type) {
	case json.RawMessage:
		return redactJSON(typed), true
	case error, fmt.Stringer:
		return slog.StringValue(Redact(fmt.Sprint(typed))), true
	case []byte:
		return slog.StringValue(Redact(string(typed))), true
	}
	switch reflect.Indirect(reflect.ValueOf(value)).Kind() {
	case reflect.Map, reflect.Slice, reflect.Array, reflect.Struct:
		raw, err := json.Marshal(value)
		if err != nil {
			return slog.StringValue(Redact(fmt.Sprintf("%+v", value))), true
		}
		return redactJSON(raw), true
	}
	return slog.Value{}, false
}

// redactJSON redacts every string and secret-keyed member of the JSON
// document raw. The result renders as JSON in the JSON handler and as a
// quoted string in the text handler.
func redactJSON(raw []byte) slog.Value {
	decoder := json.NewDecoder(bytes.NewReader(raw))
	decoder.UseNumber()
	var tree any
	var out bytes.Buffer
	encoder := json.NewEncoder(&out)
	encoder.SetEscapeHTML(false)
	if decoder.Decode(&tree) != nil || encoder.Encode(redactTree(tree)) != nil {
		return slog.StringValue(Redact(string(raw)))
	}
	return slog.AnyValue(json.RawMessage(bytes.TrimSpace(out.Bytes())))
}

func redactTree(node any) any {
	switch typed := node.(type) {
	case map[string]any:
		redacted := make(map[string]any, len(typed))
		for key, child := range typed {
			if secretKey(key) {
				redacted[Redact(key)] = "[redacted]"
				continue
			}
			redacted[Redact(key)] = redactTree(child)
		}
		return redacted
	case []any:
		for i, child := range typed {
			typed[i] = redactTree(child)
		}
	case string:
		return Redact(typed)
	}
	return node
}
