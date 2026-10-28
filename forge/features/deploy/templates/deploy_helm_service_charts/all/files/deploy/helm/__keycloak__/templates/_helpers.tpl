{{/* The release's name: Keycloak answers at http://<release>:8080, "keycloak" as in compose. */}}
{{- define "keycloak.name" -}}
{{- .Release.Name | trunc 63 | trimSuffix "-" -}}
{{- end -}}

{{/* A derived object's name: <release>-<suffix>, within 63 characters. */}}
{{- define "keycloak.child" -}}
{{- printf "%s-%s" (include "keycloak.name" .root | trunc (int (sub 62 (len .suffix))) | trimSuffix "-") .suffix -}}
{{- end -}}

{{- define "keycloak.selector" -}}
app.kubernetes.io/name: {{ .Chart.Name }}
app.kubernetes.io/instance: {{ .Release.Name }}
{{- end -}}

{{- define "keycloak.commonLabels" -}}
helm.sh/chart: {{ printf "%s-%s" .Chart.Name .Chart.Version | replace "+" "_" | trunc 63 | trimSuffix "-" }}
app.kubernetes.io/component: identity
app.kubernetes.io/part-of: {{ .Values.partOf | quote }}
app.kubernetes.io/version: {{ .Chart.AppVersion | quote }}
app.kubernetes.io/managed-by: {{ .Release.Service }}
{{- end -}}

{{- define "keycloak.labels" -}}
{{ include "keycloak.selector" . }}
{{ include "keycloak.commonLabels" . }}
{{- end -}}

{{/* The realm-sync Job's: a name of its own, so the Service never selects its pod. */}}
{{- define "keycloak.syncLabels" -}}
app.kubernetes.io/name: {{ printf "%s-realm-sync" .Chart.Name | trunc 63 | trimSuffix "-" }}
app.kubernetes.io/instance: {{ .Release.Name }}
{{ include "keycloak.commonLabels" . }}
{{- end -}}

{{/*
An image reference from {repository, tag, digest}: the digest (sha256:<64 hex>) wins over the tag.
Takes (dict "root" $ "image" <the image values> "field" <their values path>).
*/}}
{{- define "keycloak.image" -}}
{{- $digest := .image.digest | default "" -}}
{{- if $digest -}}
{{- if not (regexMatch "^sha256:[0-9a-f]{64}$" $digest) -}}
{{- fail (printf "%s: %s.digest must be sha256:<64 lowercase hex>, got %q" .root.Chart.Name .field $digest) -}}
{{- end -}}
{{- printf "%s@%s" .image.repository $digest -}}
{{- else if .root.Values.imagePolicy.requireDigest -}}
{{- fail (printf "%s: %s.digest is required (imagePolicy.requireDigest)" .root.Chart.Name .field) -}}
{{- else -}}
{{- printf "%s:%s" .image.repository (toString .image.tag) -}}
{{- end -}}
{{- end -}}

{{- define "keycloak.serviceAccountName" -}}
{{- if .Values.serviceAccount.create -}}
{{- default (include "keycloak.name" .) .Values.serviceAccount.name -}}
{{- else -}}
{{- default "default" .Values.serviceAccount.name -}}
{{- end -}}
{{- end -}}

{{- define "keycloak.adminSecret" -}}
{{- .Values.admin.existingSecret | default (include "keycloak.child" (dict "root" . "suffix" "admin")) -}}
{{- end -}}

{{/*
The realm to import: files/keycloak-realm.json with the app hosts filled in and the confidential clients' secrets
from realm.clientSecrets; "" when the chart has no realm file.
*/}}
{{- define "keycloak.realmJson" -}}
{{- $json := .Files.Get "files/keycloak-realm.json" -}}
{{- if $json -}}
{{- range $label, $host := .Values.realm.frontendHosts -}}
{{- $json = replace (printf "__APP_HOST_%s__" $label) $host $json -}}
{{- end -}}
{{- $json = replace "__APP_HOST__" (required "keycloak: realm.appHost is required" .Values.realm.appHost) $json -}}
{{- with regexFind "__APP_HOST_[A-Za-z0-9-]+__" $json -}}
{{- fail (printf "keycloak: realm.frontendHosts has no host for the placeholder %s" .) -}}
{{- end -}}
{{- $realm := fromJson $json -}}
{{- if hasKey $realm "Error" -}}
{{- fail (printf "keycloak: files/keycloak-realm.json: %s" $realm.Error) -}}
{{- end -}}
{{- $secrets := .Values.realm.clientSecrets | default dict -}}
{{- range $client := $realm.clients | default list -}}
{{- with index $secrets $client.clientId -}}
{{- $_ := set $client "secret" . -}}
{{- end -}}
{{- end -}}
{{- toPrettyJson $realm -}}
{{- end -}}
{{- end -}}
