{{/* The instance's name, the release's: `helm install redis deploy/helm/redis` answers at redis:6379 as in compose. */}}
{{- define "redis.name" -}}
{{- .Release.Name | trunc 63 | trimSuffix "-" -}}
{{- end -}}

{{/* A derived object's name: <release>-<suffix>, within 63 characters. */}}
{{- define "redis.child" -}}
{{- printf "%s-%s" (include "redis.name" .root) .suffix | trunc 63 | trimSuffix "-" -}}
{{- end -}}

{{- define "redis.selector" -}}
app.kubernetes.io/name: {{ .Chart.Name }}
app.kubernetes.io/instance: {{ .Release.Name }}
{{- end -}}

{{- define "redis.labels" -}}
helm.sh/chart: {{ printf "%s-%s" .Chart.Name .Chart.Version | replace "+" "_" | trunc 63 | trimSuffix "-" }}
{{ include "redis.selector" . }}
app.kubernetes.io/component: cache
app.kubernetes.io/part-of: {{ .Values.partOf | quote }}
app.kubernetes.io/version: {{ .Chart.AppVersion | quote }}
app.kubernetes.io/managed-by: {{ .Release.Service }}
{{- end -}}

{{/* The image; a digest pins it and wins over the tag. */}}
{{- define "redis.image" -}}
{{- if .Values.image.digest -}}
{{- if not (regexMatch "^sha256:[0-9a-f]{64}$" .Values.image.digest) -}}
{{- fail (printf "%s: image.digest must be sha256:<64 hex digits>, got %q" .Chart.Name .Values.image.digest) -}}
{{- end -}}
{{- printf "%s@%s" .Values.image.repository .Values.image.digest -}}
{{- else if .Values.imagePolicy.requireDigest -}}
{{- fail (printf "%s: image.digest is required (imagePolicy.requireDigest)" .Chart.Name) -}}
{{- else -}}
{{- printf "%s:%s" .Values.image.repository .Values.image.tag -}}
{{- end -}}
{{- end -}}

{{/* The Secret holding the password, under the key "password". */}}
{{- define "redis.secretName" -}}
{{- .Values.auth.existingSecret | default (include "redis.child" (dict "root" . "suffix" "auth")) -}}
{{- end -}}

{{/* A probe: PONG from the server. valkey-cli exits 0 on an error reply too, and authenticates with REDISCLI_AUTH. */}}
{{- define "redis.ping" -}}
exec:
  command:
    - sh
    - -c
    - test "$(valkey-cli ping)" = PONG
{{- end -}}
