package e2e_test

import (
	"bytes"
	"encoding/json"
	"net/http"
	"strconv"
	"strings"
	"syscall"
	"testing"
	"time"
)

func TestServiceBinaryIssuesInMemoryTokens(t *testing.T) {
	port := freePort(t)
	server := command(t, "server", append(serverEnv(t), "ENV=testing", "PORT="+strconv.Itoa(port),
		"APP__SECURITY__AUTH__ENABLED=true", "GATEKEEPER_ISSUER=", "SERVICE_AUDIENCE=svc-e2e", "TENANT_ID_CLAIM=")...)
	if err := server.Start(); err != nil {
		t.Fatal(err)
	}
	base := "http://127.0.0.1:" + strconv.Itoa(port) + "/api/v1"
	ready := false
	for range 200 {
		if response, err := http.Get(base + "/health/live"); err == nil {
			response.Body.Close()
			ready = true
			break
		}
		time.Sleep(25 * time.Millisecond)
	}
	if !ready {
		_ = server.Process.Kill()
		_ = server.Wait()
		t.Fatal("server did not become live within 5s")
	}
	call := func(method, path, token string, payload any) (int, map[string]any) {
		var body bytes.Buffer
		if payload != nil {
			_ = json.NewEncoder(&body).Encode(payload)
		}
		request, _ := http.NewRequest(method, base+path, &body)
		if token != "" {
			request.Header.Set("Authorization", "Bearer "+token)
		}
		response, err := http.DefaultClient.Do(request)
		if err != nil {
			return 0, nil
		}
		defer response.Body.Close()
		decoded := map[string]any{}
		_ = json.NewDecoder(response.Body).Decode(&decoded)
		return response.StatusCode, decoded
	}
	mintStatus, minted := call(http.MethodPost, "/dev/auth/token", "", map[string]any{"sub": "e2e-user"})
	token, _ := minted["access_token"].(string)
	validStatus, _ := call(http.MethodGet, "/missing", token, nil)
	anonymousStatus, _ := call(http.MethodGet, "/missing", "", nil)
	jwksStatus, _ := call(http.MethodGet, "/dev/auth/jwks", "", nil)
	_ = server.Process.Signal(syscall.SIGTERM)
	err := server.Wait()
	if mintStatus != http.StatusOK || token == "" {
		t.Fatalf("mint: %d %v", mintStatus, minted)
	}
	if validStatus != http.StatusNotFound || anonymousStatus != http.StatusUnauthorized || jwksStatus != http.StatusOK {
		t.Fatalf("minted=%d anonymous=%d jwks=%d", validStatus, anonymousStatus, jwksStatus)
	}
	if err != nil {
		t.Fatalf("graceful shutdown must exit 0: %v", err)
	}

	for _, env := range []string{"production", "ci"} {
		refused := command(t, "server", "ENV="+env, "PORT="+strconv.Itoa(freePort(t)), "APP__SECURITY__AUTH__ENABLED=false")
		output, err := refused.CombinedOutput()
		if err == nil || !strings.Contains(string(output), "refuses to start") {
			t.Fatalf("ENV=%s must stop boot: %v\n%s", env, err, output)
		}
	}
}
