{{/* The cluster's name, the release's: the operator names its Services <release>-rw (the primary), -ro and -r. */}}
{{- define "postgres.name" -}}
{{- .Release.Name | trunc 63 | trimSuffix "-" -}}
{{- end -}}

{{/* A derived object's name: <release>-<suffix>, within 63 characters. */}}
{{- define "postgres.child" -}}
{{- printf "%s-%s" (include "postgres.name" .root) .suffix | trunc 63 | trimSuffix "-" -}}
{{- end -}}

{{/* The Secret with a role's password: <release>-<role> with "-" for "_". */}}
{{- define "postgres.roleSecret" -}}
{{- include "postgres.child" (dict "root" .root "suffix" (.role | replace "_" "-")) -}}
{{- end -}}

{{- define "postgres.selector" -}}
app.kubernetes.io/name: {{ .Chart.Name }}
app.kubernetes.io/instance: {{ .Release.Name }}
{{- end -}}

{{- define "postgres.labels" -}}
helm.sh/chart: {{ printf "%s-%s" .Chart.Name .Chart.Version | replace "+" "_" | trunc 63 | trimSuffix "-" }}
{{ include "postgres.selector" . }}
app.kubernetes.io/component: database
app.kubernetes.io/part-of: {{ .Values.partOf }}
app.kubernetes.io/version: {{ .Chart.AppVersion | quote }}
app.kubernetes.io/managed-by: {{ .Release.Service }}
{{- end -}}

{{/* The image; a digest pins it, after the tag, from which the operator reads the PostgreSQL major version. */}}
{{- define "postgres.image" -}}
{{- $image := .Values.cluster.image -}}
{{- if $image.digest -}}
{{- if not (regexMatch "^sha256:[0-9a-f]{64}$" $image.digest) -}}
{{- fail (printf "%s: image.digest must be sha256:<64 hex digits>, got %q" .Chart.Name $image.digest) -}}
{{- end -}}
{{- if $image.tag -}}
{{- printf "%s:%s@%s" $image.repository $image.tag $image.digest -}}
{{- else -}}
{{- printf "%s@%s" $image.repository $image.digest -}}
{{- end -}}
{{- else if .Values.imagePolicy.requireDigest -}}
{{- fail (printf "%s: image.digest is required (imagePolicy.requireDigest)" .Chart.Name) -}}
{{- else -}}
{{- printf "%s:%s" $image.repository $image.tag -}}
{{- end -}}
{{- end -}}

{{/* Names CloudNativePG refuses or takes for itself, caught before the operator sees them. */}}
{{- define "postgres.validate" -}}
{{- $roles := list -}}
{{- range .Values.roles -}}
{{- if or (has .name (list "postgres" "streaming_replica")) (hasPrefix "pg_" .name) (hasPrefix "cnpg_" .name) -}}
{{- fail (printf "%s: role %q is reserved by PostgreSQL or CloudNativePG; rename the backend it comes from" $.Chart.Name .name) -}}
{{- end -}}
{{- if has .name (list "ca" "server" "replication" "superuser") -}}
{{- fail (printf "%s: the Secret of role %q would be %s, a name CloudNativePG gives its own Secret; rename the backend it comes from" $.Chart.Name .name (include "postgres.roleSecret" (dict "root" $ "role" .name))) -}}
{{- end -}}
{{- $roles = append $roles .name -}}
{{- end -}}
{{- range .Values.databases -}}
{{- if not (has .owner $roles) -}}
{{- fail (printf "%s: database %q is owned by %q, which is not one of the roles" $.Chart.Name .name .owner) -}}
{{- end -}}
{{- end -}}
{{- end -}}
