{{/* The release's name: the gatekeeper answers at http://<release>:<port>, "gatekeeper" as in compose. */}}
{{- define "gatekeeper.name" -}}
{{- .Release.Name | trunc 63 | trimSuffix "-" -}}
{{- end -}}

{{/* A derived object's name: <release>-<suffix>, within 63 characters. */}}
{{- define "gatekeeper.child" -}}
{{- printf "%s-%s" (include "gatekeeper.name" .root | trunc (int (sub 62 (len .suffix))) | trimSuffix "-") .suffix -}}
{{- end -}}

{{- define "gatekeeper.selector" -}}
app.kubernetes.io/name: {{ .Chart.Name }}
app.kubernetes.io/instance: {{ .Release.Name }}
{{- end -}}

{{- define "gatekeeper.labels" -}}
{{ include "gatekeeper.selector" . }}
helm.sh/chart: {{ printf "%s-%s" .Chart.Name .Chart.Version | replace "+" "_" | trunc 63 | trimSuffix "-" }}
app.kubernetes.io/component: auth
app.kubernetes.io/part-of: {{ .Values.partOf | quote }}
app.kubernetes.io/version: {{ .Chart.AppVersion | quote }}
app.kubernetes.io/managed-by: {{ .Release.Service }}
{{- end -}}

{{/* The image reference: the digest (sha256:<64 hex>) wins over the tag. */}}
{{- define "gatekeeper.image" -}}
{{- $digest := .Values.image.digest | default "" -}}
{{- if $digest -}}
{{- if not (regexMatch "^sha256:[0-9a-f]{64}$" $digest) -}}
{{- fail (printf "%s: image.digest must be sha256:<64 lowercase hex>, got %q" .Chart.Name $digest) -}}
{{- end -}}
{{- printf "%s@%s" .Values.image.repository $digest -}}
{{- else if .Values.imagePolicy.requireDigest -}}
{{- fail (printf "%s: image.digest is required (imagePolicy.requireDigest)" .Chart.Name) -}}
{{- else -}}
{{- printf "%s:%s" .Values.image.repository (toString .Values.image.tag) -}}
{{- end -}}
{{- end -}}

{{- define "gatekeeper.serviceAccountName" -}}
{{- if .Values.serviceAccount.create -}}
{{- default (include "gatekeeper.name" .) .Values.serviceAccount.name -}}
{{- else -}}
{{- default "default" .Values.serviceAccount.name -}}
{{- end -}}
{{- end -}}

{{/* Where the gatekeeper reads its signing keys and S2S registry: its own env says. */}}
{{- define "gatekeeper.signingKeyDir" -}}
{{- dig "SIGNING_KEY_DIR" "/run/secrets/gatekeeper-signing" (.Values.configMap.data | default dict) -}}
{{- end -}}

{{- define "gatekeeper.registryPath" -}}
{{- dig "SERVICE_REGISTRY_PATH" "/run/secrets/gatekeeper-service-registry/service_registry.yaml" (.Values.configMap.data | default dict) -}}
{{- end -}}
