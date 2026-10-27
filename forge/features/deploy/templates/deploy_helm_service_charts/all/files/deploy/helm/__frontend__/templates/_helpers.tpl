{{/*
The service's name: the release's, so `helm install <service> deploy/helm/<service>` names every object after the
service, and its Service answers at http://<service>:<port> as in compose.
*/}}
{{- define "svc.name" -}}
{{- .Release.Name | trunc 63 | trimSuffix "-" -}}
{{- end -}}

{{/* A derived object's name: <service>-<suffix>, within 63 characters. */}}
{{- define "svc.child" -}}
{{- printf "%s-%s" (include "svc.name" .root | trunc (int (sub 62 (len .suffix))) | trimSuffix "-") .suffix -}}
{{- end -}}

{{- define "svc.selector" -}}
app.kubernetes.io/name: {{ include "svc.name" . }}
app.kubernetes.io/instance: {{ .Release.Name }}
{{- end -}}

{{- define "svc.labels" -}}
helm.sh/chart: {{ printf "%s-%s" .Chart.Name .Chart.Version | replace "+" "_" | trunc 63 | trimSuffix "-" }}
{{ include "svc.selector" . }}
app.kubernetes.io/component: frontend
app.kubernetes.io/part-of: {{ .Values.partOf }}
app.kubernetes.io/managed-by: {{ .Release.Service }}
{{- end -}}
