package e2e_test

import (
	"strings"
	"testing"
)

func TestBinariesRedactLogsBeforeConfigurationLoads(t *testing.T) {
	_, out, code := migrateWithEnv(t, nil, "token=SECRET99")
	if code != 2 || strings.Contains(out, "SECRET99") {
		t.Fatalf("an early log line must be redacted: exit %d\n%s", code, out)
	}
}
