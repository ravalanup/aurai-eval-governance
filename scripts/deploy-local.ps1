<#
.SYNOPSIS
  Install the observability stack (Langfuse + Phoenix + trace fan-out) on the local kind cluster.

.DESCRIPTION
  Prerequisites: aurai-infra is running (local\scripts\up.ps1) with Docker, kind, kubectl and Helm installed.
  Secrets are generated once and reused on re-runs; they are sent to the cluster over stdin, never written to disk.

  Order: our chart (stores, db bootstrap) -> Langfuse -> Phoenix -> fan-out collector -> forward from the
  platform collector. Chart versions are pinned through parameters; leave empty to use the latest and
  review the result.
#>
param(
  [string]$ClusterName = "aurai",
  [string]$Namespace = "aurai-observability",
  [string]$InfraNamespace = "aurai-system",
  [string]$InfraPath = "..\aurai-infra",
  [string]$LangfuseChartVersion = "",
  [string]$PhoenixChartVersion = ""
)

$ErrorActionPreference = "Stop"
$repo = Split-Path -Parent $PSScriptRoot

foreach ($tool in "kubectl", "helm") {
  if (-not (Get-Command $tool -ErrorAction SilentlyContinue)) { throw "Missing required tool: $tool" }
}
if (-not $LangfuseChartVersion) { Write-Warning "LangfuseChartVersion not pinned - installing the latest chart." }
if (-not $PhoenixChartVersion)  { Write-Warning "PhoenixChartVersion not pinned - installing the latest chart." }

kubectl config use-context "kind-$ClusterName" | Out-Null
kubectl get namespace $Namespace 2>$null | Out-Null
if ($LASTEXITCODE -ne 0) { throw "Namespace $Namespace not found. Start the platform first: aurai-infra\local\scripts\up.ps1" }

function New-RandomHex([int]$bytes) {
  $buffer = New-Object byte[] $bytes
  [System.Security.Cryptography.RandomNumberGenerator]::Create().GetBytes($buffer)
  -join ($buffer | ForEach-Object { $_.ToString("x2") })
}
function Get-SecretValue([string]$secret, [string]$key) {
  $encoded = kubectl get secret $secret -n $Namespace -o "jsonpath={.data.$key}" 2>$null
  if ($LASTEXITCODE -eq 0 -and $encoded) { return [System.Text.Encoding]::UTF8.GetString([Convert]::FromBase64String($encoded)) }
  return $null
}
function Get-OrNew([string]$secret, [string]$key, [scriptblock]$make) {
  $v = Get-SecretValue $secret $key
  if ($v) { return $v }
  return (& $make)
}

# Local-only credentials. Postgres matches aurai-infra values-local.yaml.
$pgUrl     = "postgresql://aurai:aurai-local-dev-only@postgres.$InfraNamespace.svc.cluster.local:5432"
$chPass    = Get-OrNew "observability-secrets" "clickhouse-password" { New-RandomHex 12 }
$redisPass = Get-OrNew "observability-secrets" "redis-password" { New-RandomHex 12 }
$s3Access  = Get-OrNew "observability-secrets" "s3-access-key" { "aurai" + (New-RandomHex 4) }
$s3Secret  = Get-OrNew "observability-secrets" "s3-secret-key" { New-RandomHex 16 }
$pk        = Get-OrNew "langfuse-secrets" "public-key" { "pk-lf-" + (New-RandomHex 12) }
$sk        = Get-OrNew "langfuse-secrets" "secret-key" { "sk-lf-" + (New-RandomHex 12) }
$basic     = "Basic " + [Convert]::ToBase64String([System.Text.Encoding]::UTF8.GetBytes("${pk}:${sk}"))

@"
apiVersion: v1
kind: Secret
metadata: { name: observability-secrets, namespace: $Namespace }
type: Opaque
stringData:
  database-admin-url: "$pgUrl/postgres"
  clickhouse-password: "$chPass"
  redis-password: "$redisPass"
  s3-access-key: "$s3Access"
  s3-secret-key: "$s3Secret"
  langfuse-basic-auth: "$basic"
---
apiVersion: v1
kind: Secret
metadata: { name: langfuse-secrets, namespace: $Namespace }
type: Opaque
stringData:
  salt: "$(Get-OrNew 'langfuse-secrets' 'salt' { New-RandomHex 16 })"
  nextauth-secret: "$(Get-OrNew 'langfuse-secrets' 'nextauth-secret' { New-RandomHex 16 })"
  encryption-key: "$(Get-OrNew 'langfuse-secrets' 'encryption-key' { New-RandomHex 32 })"
  postgres-password: "aurai-local-dev-only"
  public-key: "$pk"
  secret-key: "$sk"
  admin-password: "$(Get-OrNew 'langfuse-secrets' 'admin-password' { New-RandomHex 8 })"
"@ | kubectl apply -f - | Out-Null
Write-Host "Secrets applied"

Write-Host "Installing stores and database bootstrap"
helm upgrade --install aurai-observability "$repo\charts\aurai-observability" `
  --namespace $Namespace -f "$repo\charts\aurai-observability\values-local.yaml" --wait --timeout 10m
if ($LASTEXITCODE -ne 0) { throw "aurai-observability install failed" }
kubectl wait --for=condition=complete job/create-databases -n $Namespace --timeout=180s
if ($LASTEXITCODE -ne 0) { throw "database bootstrap did not complete" }

Write-Host "Installing Langfuse"
helm repo add langfuse https://langfuse.github.io/langfuse-k8s 2>$null | Out-Null
helm repo update langfuse | Out-Null
$lfArgs = @()
if ($LangfuseChartVersion) { $lfArgs += @("--version", $LangfuseChartVersion) }
helm upgrade --install langfuse langfuse/langfuse --namespace $Namespace `
  -f "$repo\deploy\langfuse-values.yaml" @lfArgs --wait --timeout 15m
if ($LASTEXITCODE -ne 0) { throw "Langfuse install failed" }

Write-Host "Installing Phoenix"
$phArgs = @()
if ($PhoenixChartVersion) { $phArgs += @("--version", $PhoenixChartVersion) }
helm upgrade --install phoenix oci://registry-1.docker.io/arizephoenix/phoenix-helm --namespace $Namespace `
  -f "$repo\deploy\phoenix-values.yaml" `
  --set-string "database.url=$pgUrl/phoenix" @phArgs --wait --timeout 10m
if ($LASTEXITCODE -ne 0) { throw "Phoenix install failed" }

# The Phoenix service name comes from its chart, so find the service that exposes the OTLP gRPC port.
$phoenixSvc = kubectl get svc -n $Namespace -l "app.kubernetes.io/instance=phoenix" `
  -o jsonpath='{range .items[*]}{.metadata.name}{" "}{range .spec.ports[*]}{.port}{","}{end}{"\n"}{end}' |
  Where-Object { $_ -match "4317" } | Select-Object -First 1
if (-not $phoenixSvc) { throw "Could not find a Phoenix service exposing port 4317. Check: kubectl get svc -n $Namespace" }
$phoenixSvc = ($phoenixSvc -split " ")[0]
Write-Host "Phoenix OTLP service: $phoenixSvc"

Write-Host "Enabling the trace fan-out collector"
helm upgrade --install aurai-observability "$repo\charts\aurai-observability" `
  --namespace $Namespace -f "$repo\charts\aurai-observability\values-local.yaml" `
  --set fanout.enabled=true --set-string "fanout.phoenixEndpoint=${phoenixSvc}.${Namespace}.svc.cluster.local:4317" `
  --wait --timeout 5m
if ($LASTEXITCODE -ne 0) { throw "fan-out install failed" }

$infra = Join-Path $repo $InfraPath
if (Test-Path "$infra\charts\aurai-platform") {
  Write-Host "Forwarding platform traces to the fan-out collector"
  helm upgrade aurai-platform "$infra\charts\aurai-platform" --namespace $InfraNamespace `
    --reuse-values -f "$repo\deploy\infra-collector-forward.values.yaml" --wait --timeout 5m
  if ($LASTEXITCODE -ne 0) { throw "platform collector upgrade failed" }
} else {
  Write-Warning "aurai-infra not found at $infra. Apply deploy\infra-collector-forward.values.yaml to the aurai-platform release yourself."
}

Write-Host ""
Write-Host "Observability stack installed. Next:"
Write-Host "  .\scripts\port-forward.ps1      # Langfuse http://localhost:3100, Phoenix http://localhost:6006"
Write-Host "  Langfuse login: admin@amazing-enterprise.example; password:"
Write-Host "  kubectl get secret langfuse-secrets -n $Namespace -o jsonpath='{.data.admin-password}' | % { [Text.Encoding]::UTF8.GetString([Convert]::FromBase64String(`$_)) }"
