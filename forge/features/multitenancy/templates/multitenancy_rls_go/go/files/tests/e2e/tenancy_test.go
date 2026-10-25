package e2e_test

import (
	"strings"
	"testing"
)

func TestServiceBinaryRejectsInvalidTenancyConfiguration(t *testing.T) {
	for _, setting := range []string{"TENANT_RESOLUTION=sideways", "TENANT_RLS_GUC=app.tenant"} {
		server := command(t, "server", "ENV=testing", setting)
		out, err := server.CombinedOutput()
		if err == nil || server.ProcessState.ExitCode() != 1 {
			t.Fatalf("%s must stop boot: %v\n%s", setting, err, out)
		}
		if name, _, _ := strings.Cut(setting, "="); !strings.Contains(string(out), name) {
			t.Fatalf("the error names %s:\n%s", name, out)
		}
	}
}
