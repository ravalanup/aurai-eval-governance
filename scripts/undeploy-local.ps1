<# Remove the observability releases. Data volumes and secrets are kept unless -Purge is given. #>
param([string]$Namespace = "aurai-observability", [switch]$Purge)
foreach ($r in "phoenix", "langfuse", "aurai-observability") { helm uninstall $r --namespace $Namespace 2>$null }
if ($Purge) {
  kubectl delete pvc --all -n $Namespace
  kubectl delete secret observability-secrets langfuse-secrets -n $Namespace --ignore-not-found
}
Write-Host "Done. The platform collector still forwards to otel-fanout; re-apply aurai-infra values to stop that."
