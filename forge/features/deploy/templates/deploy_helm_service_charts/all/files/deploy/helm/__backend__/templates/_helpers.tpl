{{/* Names, labels and images of a backend chart. The release is named after the service, so its Service answers at
     http://<service>:<port> as in compose. */}}

{{- define "svc.fullname" -}}
{{- .Release.Name | trunc 63 | trimSuffix "-" -}}
{{- end -}}

{{/* A child object's name: <release>-<suffix>, the release part shortened so the whole suffix fits in 63 characters.
     Call with (dict "root" $ "suffix" "config"). */}}
{{- define "svc.child" -}}
{{- printf "%s-%s" (include "svc.fullname" .root | trunc (int (sub 62 (len .suffix))) | trimSuffix "-") .suffix -}}
{{- end -}}

{{- define "svc.selectorLabels" -}}
app.kubernetes.io/name: {{ .Chart.Name }}
app.kubernetes.io/instance: {{ .Release.Name }}
{{- end -}}

{{- define "svc.labels" -}}
helm.sh/chart: {{ printf "%s-%s" .Chart.Name .Chart.Version | replace "+" "_" | trunc 63 | trimSuffix "-" }}
{{ include "svc.selectorLabels" . }}
app.kubernetes.io/component: backend
app.kubernetes.io/part-of: {{ .Values.partOf }}
app.kubernetes.io/version: {{ .Chart.AppVersion | quote }}
app.kubernetes.io/managed-by: {{ .Release.Service }}
{{- end -}}

{{- define "svc.podLabels" -}}
{{ include "svc.selectorLabels" . }}
app.kubernetes.io/component: backend
app.kubernetes.io/part-of: {{ .Values.partOf }}
{{- with .Values.deployment.podLabels }}
{{ toYaml . }}
{{- end }}
{{- end -}}

{{/* A hook or test pod's labels: a name of its own, so no Service, PDB or NetworkPolicy of the release selects it.
     Call with (dict "root" $ "suffix" "migrate" "component" "migrate"). */}}
{{- define "svc.processLabels" -}}
app.kubernetes.io/name: {{ include "svc.child" (dict "root" .root "suffix" .suffix) }}
app.kubernetes.io/instance: {{ .root.Release.Name }}
app.kubernetes.io/component: {{ .component }}
app.kubernetes.io/part-of: {{ .root.Values.partOf }}
app.kubernetes.io/managed-by: {{ .root.Release.Service }}
{{- end -}}

{{/* The annotations of a pre-install/pre-upgrade hook. Call with the weight. */}}
{{- define "svc.hookAnnotations" -}}
"helm.sh/hook": pre-install,pre-upgrade
"helm.sh/hook-weight": {{ . | quote }}
"helm.sh/hook-delete-policy": before-hook-creation,hook-succeeded
{{- end -}}

{{/* The service's image: repository@digest when image.digest is set, else repository:tag. */}}
{{- define "svc.image" -}}
{{- $image := .Values.image -}}
{{- $repository := required (printf "%s: image.repository is required" .Chart.Name) $image.repository -}}
{{- if $image.digest -}}
{{- if not (regexMatch "^sha256:[0-9a-f]{64}$" $image.digest) -}}
{{- fail (printf "%s: image.digest must be sha256:<64 hex>, not %q" .Chart.Name $image.digest) -}}
{{- end -}}
{{- printf "%s@%s" $repository $image.digest -}}
{{- else if .Values.imagePolicy.requireDigest -}}
{{- fail (printf "%s: image.digest is required (imagePolicy.requireDigest)" .Chart.Name) -}}
{{- else -}}
{{- printf "%s:%s" $repository (toString (required (printf "%s: image.tag or image.digest is required" .Chart.Name) $image.tag)) -}}
{{- end -}}
{{- end -}}

{{/* A helper image given as one reference (migration.postgresImage, test.image), pinned under
     imagePolicy.requireDigest. Call with (dict "root" $ "key" "test.image" "image" <reference>). */}}
{{- define "svc.auxImage" -}}
{{- $image := required (printf "%s: %s is required" .root.Chart.Name .key) .image -}}
{{- if and .root.Values.imagePolicy.requireDigest (not (regexMatch "@sha256:[0-9a-f]{64}$" $image)) -}}
{{- fail (printf "%s: %s needs a digest (@sha256:<64 hex>) under imagePolicy.requireDigest, not %q" .root.Chart.Name .key $image) -}}
{{- end -}}
{{- $image -}}
{{- end -}}

{{- define "svc.serviceAccountName" -}}
{{- if .Values.serviceAccount.create -}}
{{- default (include "svc.fullname" .) .Values.serviceAccount.name -}}
{{- else -}}
{{- .Values.serviceAccount.name -}}
{{- end -}}
{{- end -}}

{{/* A NetworkPolicy peer as JSON, from {namespace?, podLabels}: a pod selector in the release's namespace, or in the
     namespace named by its kubernetes.io/metadata.name label. */}}
{{- define "svc.peer" -}}
{{- $peer := dict "podSelector" (dict "matchLabels" (.podLabels | default dict)) -}}
{{- with .namespace -}}
{{- $_ := set $peer "namespaceSelector" (dict "matchLabels" (dict "kubernetes.io/metadata.name" .)) -}}
{{- end -}}
{{- toJson $peer -}}
{{- end -}}

{{/* A list of port numbers as a JSON array of TCP NetworkPolicy ports. */}}
{{- define "svc.ports" -}}
{{- $out := list -}}
{{- range . -}}
{{- $out = append $out (dict "port" (int .) "protocol" "TCP") -}}
{{- end -}}
{{- toJson $out -}}
{{- end -}}

{{/* The ExternalSecret spec of the Secret <release>-<target>. Call with (dict "root" $ "target" "secret"). */}}
{{- define "svc.externalSecretSpec" -}}
{{- $es := .root.Values.externalSecret -}}
{{- if not (or $es.data $es.dataFrom) -}}
{{- fail (printf "%s: externalSecret needs data or dataFrom" .root.Chart.Name) -}}
{{- end -}}
secretStoreRef:
  kind: {{ $es.secretStoreRef.kind }}
  name: {{ required (printf "%s: externalSecret.secretStoreRef.name is required" .root.Chart.Name) $es.secretStoreRef.name }}
refreshInterval: {{ $es.refreshInterval }}
target:
  name: {{ include "svc.child" (dict "root" .root "suffix" .target) }}
  creationPolicy: Owner
{{- with $es.data }}
data:
  {{- toYaml . | nindent 2 }}
{{- end }}
{{- with $es.dataFrom }}
dataFrom:
  {{- toYaml . | nindent 2 }}
{{- end }}
{{- end -}}

{{/* The migrate container: the migration command with the service's env from the hook copies, and, under
     migration.dbAppRole, the owner's credentials over the runtime role's. */}}
{{- define "svc.migrateContainer" -}}
{{- $m := .Values.migration -}}
{{- if not $m.command -}}
{{- fail (printf "%s: migration.command is required while migration.enabled" .Chart.Name) -}}
{{- end -}}
- name: migrate
  image: {{ include "svc.image" . | quote }}
  imagePullPolicy: {{ .Values.image.pullPolicy }}
  command: {{ toJson $m.command }}
  envFrom:
    - configMapRef:
        name: {{ include "svc.child" (dict "root" . "suffix" "migrate-config") }}
    - secretRef:
        {{- if or .Values.secret.enabled .Values.externalSecret.enabled }}
        name: {{ include "svc.child" (dict "root" . "suffix" "migrate-secret") }}
        {{- else }}
        # secret.enabled false: the Secret of that name provided outside the chart
        name: {{ include "svc.child" (dict "root" . "suffix" "secret") }}
        {{- end }}
        {{- /* an ExternalSecret's copy appears once synced: the pod waits for it */}}
        optional: {{ not .Values.externalSecret.enabled }}
    {{- with .Values.container.envFrom }}
    {{- toYaml . | nindent 4 }}
    {{- end }}
    {{- if $m.dbAppRole }}
    - secretRef:
        name: {{ include "svc.child" (dict "root" . "suffix" "migrate-owner") }}
    {{- end }}
  securityContext:
    {{- toYaml .Values.securityContext | nindent 4 }}
{{- end -}}
