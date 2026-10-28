package e2e_test

import (
	"encoding/json"
	"net/http"
	"strconv"
	"syscall"
	"testing"
	"time"

	"forge.local/platform-auth-go/authtest"
)

func TestServiceBinaryVerifiesPlatformTokens(t *testing.T) {
	issuer := authtest.NewIssuer()
	defer issuer.Close()
	port := freePort(t)
	server := command(t, "server", append(serverEnv(t), "ENV=testing", "PORT="+strconv.Itoa(port), "APP__SECURITY__AUTH__ENABLED=true",
		"GATEKEEPER_ISSUER="+issuer.URL(), "SERVICE_AUDIENCE=svc-e2e", "TENANT_ID_CLAIM=")...)
	if err := server.Start(); err != nil {
		t.Fatal(err)
	}
	base := "http://127.0.0.1:" + strconv.Itoa(port) + "/api/v1"
	for range 200 {
		if response, err := http.Get(base + "/health/live"); err == nil {
			response.Body.Close()
			break
		}
		time.Sleep(25 * time.Millisecond)
	}
	call := func(path, token string) (int, string, map[string]any) {
		request, _ := http.NewRequest(http.MethodGet, base+path, nil)
		if token != "" {
			request.Header.Set("Authorization", "Bearer "+token)
		}
		response, err := http.DefaultClient.Do(request)
		if err != nil {
			return 0, "", nil
		}
		defer response.Body.Close()
		body := map[string]any{}
		_ = json.NewDecoder(response.Body).Decode(&body)
		return response.StatusCode, response.Header.Get("WWW-Authenticate"), body
	}
	tenant := "55555555-5555-4555-8555-555555555555"
	anonymousStatus, challenge, anonymous := call("/missing", "")
	validStatus, _, _ := call("/missing", issuer.Token(issuer.Options("svc-e2e", tenant, tenant)))
	foreignStatus, _, _ := call("/missing", issuer.Token(issuer.Options("svc-other", tenant, tenant)))
	infoStatus, _, _ := call("/info", "")
	_ = server.Process.Signal(syscall.SIGTERM)
	err := server.Wait()
	if anonymousStatus != http.StatusUnauthorized || challenge != "Bearer" || anonymous["title"] != "invalid_token" {
		t.Fatalf("anonymous request: %d %q %v", anonymousStatus, challenge, anonymous)
	}
	if validStatus != http.StatusNotFound || foreignStatus != http.StatusUnauthorized || infoStatus != http.StatusOK {
		t.Fatalf("valid=%d foreign-audience=%d info=%d", validStatus, foreignStatus, infoStatus)
	}
	if err != nil {
		t.Fatalf("graceful shutdown must exit 0: %v", err)
	}

	misconfigured := command(t, "server", "ENV=testing", "PORT="+strconv.Itoa(freePort(t)), "APP__SECURITY__AUTH__ENABLED=true",
		"GATEKEEPER_ISSUER=", "SERVICE_AUDIENCE=")
	if err := misconfigured.Run(); err == nil {
		t.Fatal("enabled auth without an issuer must stop boot")
	}
}
