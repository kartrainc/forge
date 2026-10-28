{{/* Names, labels and images of a frontend chart: nginx serving the built SPA. */}}

{{- define "svc.fullname" -}}
{{- .Release.Name | trunc 63 | trimSuffix "-" -}}
{{- end -}}

{{/* A child object's name: <release>-<suffix>, the release part shortened so the whole suffix fits in 63 characters.
     Call with (dict "root" $ "suffix" "nginx"). */}}
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
app.kubernetes.io/component: frontend
app.kubernetes.io/part-of: {{ .Values.partOf | quote }}
app.kubernetes.io/version: {{ .Chart.AppVersion | quote }}
app.kubernetes.io/managed-by: {{ .Release.Service }}
{{- end -}}

{{- define "svc.podLabels" -}}
{{ include "svc.selectorLabels" . }}
app.kubernetes.io/component: frontend
app.kubernetes.io/part-of: {{ .Values.partOf | quote }}
{{- with .Values.deployment.podLabels }}
{{ toYaml . }}
{{- end }}
{{- end -}}

{{/* The test pod's labels: a name of its own, so no Service, PDB or NetworkPolicy of the release selects it. */}}
{{- define "svc.testLabels" -}}
app.kubernetes.io/name: {{ include "svc.child" (dict "root" . "suffix" "test") }}
app.kubernetes.io/instance: {{ .Release.Name }}
app.kubernetes.io/component: test
app.kubernetes.io/part-of: {{ .Values.partOf | quote }}
app.kubernetes.io/managed-by: {{ .Release.Service }}
{{- end -}}

{{/* The frontend's image: repository@digest when image.digest is set, else repository:tag. */}}
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

{{/* A helper image given as one reference (test.image), pinned under imagePolicy.requireDigest.
     Call with (dict "root" $ "key" "test.image" "image" <reference>). */}}
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
