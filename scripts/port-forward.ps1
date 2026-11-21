<# Port-forward Langfuse (3100) and Phoenix (6006). Ctrl+C stops both. #>
param([string]$Namespace = "aurai-observability")
$ErrorActionPreference = "Stop"
$phoenix = kubectl get svc -n $Namespace -l "app.kubernetes.io/instance=phoenix" -o jsonpath='{.items[0].metadata.name}'
$jobs = @(
  Start-Job { kubectl port-forward -n $using:Namespace svc/langfuse-web 3100:3000 },
  Start-Job { kubectl port-forward -n $using:Namespace "svc/$using:phoenix" 6006:6006 }
)
Write-Host "Langfuse http://localhost:3100   Phoenix http://localhost:6006   (Ctrl+C to stop)"
try { Wait-Job $jobs | Out-Null } finally { $jobs | Stop-Job; $jobs | Remove-Job }
