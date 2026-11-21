#!/usr/bin/env bash
# Install Langfuse + Phoenix + trace fan-out on the local kind cluster. Mirrors deploy-local.ps1.
set -euo pipefail
CLUSTER="${CLUSTER:-aurai}"; NS="${NS:-aurai-observability}"; INFRA_NS="${INFRA_NS:-aurai-system}"
REPO="$(cd "$(dirname "$0")/.." && pwd)"; INFRA="${INFRA_PATH:-$REPO/../aurai-infra}"
LF_VER="${LANGFUSE_CHART_VERSION:-}"; PH_VER="${PHOENIX_CHART_VERSION:-}"
for t in kubectl helm openssl; do command -v "$t" >/dev/null || { echo "missing $t" >&2; exit 1; }; done
[ -n "$LF_VER" ] || echo "warning: LANGFUSE_CHART_VERSION not pinned" >&2
[ -n "$PH_VER" ] || echo "warning: PHOENIX_CHART_VERSION not pinned" >&2
kubectl config use-context "kind-$CLUSTER" >/dev/null
kubectl get namespace "$NS" >/dev/null 2>&1 || { echo "namespace $NS missing; run aurai-infra/local/scripts/up.sh" >&2; exit 1; }

get() { kubectl get secret "$1" -n "$NS" -o "jsonpath={.data.$2}" 2>/dev/null | base64 -d 2>/dev/null || true; }
keep() { local v; v="$(get "$1" "$2")"; if [ -n "$v" ]; then echo "$v"; else echo "$3"; fi; }
PG="postgresql://aurai:aurai-local-dev-only@postgres.$INFRA_NS.svc.cluster.local:5432"
PK="$(keep langfuse-secrets public-key "pk-lf-$(openssl rand -hex 12)")"
SK="$(keep langfuse-secrets secret-key "sk-lf-$(openssl rand -hex 12)")"
BASIC="Basic $(printf '%s:%s' "$PK" "$SK" | base64 | tr -d '\n')"

kubectl apply -f - >/dev/null <<YAML
apiVersion: v1
kind: Secret
metadata: { name: observability-secrets, namespace: $NS }
type: Opaque
stringData:
  database-admin-url: "$PG/postgres"
  clickhouse-password: "$(keep observability-secrets clickhouse-password "$(openssl rand -hex 12)")"
  redis-password: "$(keep observability-secrets redis-password "$(openssl rand -hex 12)")"
  s3-access-key: "$(keep observability-secrets s3-access-key "aurai$(openssl rand -hex 4)")"
  s3-secret-key: "$(keep observability-secrets s3-secret-key "$(openssl rand -hex 16)")"
  langfuse-basic-auth: "$BASIC"
---
apiVersion: v1
kind: Secret
metadata: { name: langfuse-secrets, namespace: $NS }
type: Opaque
stringData:
  salt: "$(keep langfuse-secrets salt "$(openssl rand -hex 16)")"
  nextauth-secret: "$(keep langfuse-secrets nextauth-secret "$(openssl rand -hex 16)")"
  encryption-key: "$(keep langfuse-secrets encryption-key "$(openssl rand -hex 32)")"
  postgres-password: "aurai-local-dev-only"
  public-key: "$PK"
  secret-key: "$SK"
  admin-password: "$(keep langfuse-secrets admin-password "$(openssl rand -hex 8)")"
YAML

helm upgrade --install aurai-observability "$REPO/charts/aurai-observability" -n "$NS" \
  -f "$REPO/charts/aurai-observability/values-local.yaml" --wait --timeout 10m
kubectl wait --for=condition=complete job/create-databases -n "$NS" --timeout=180s

helm repo add langfuse https://langfuse.github.io/langfuse-k8s >/dev/null 2>&1 || true
helm repo update langfuse >/dev/null
helm upgrade --install langfuse langfuse/langfuse -n "$NS" -f "$REPO/deploy/langfuse-values.yaml" \
  ${LF_VER:+--version "$LF_VER"} --wait --timeout 15m

helm upgrade --install phoenix oci://registry-1.docker.io/arizephoenix/phoenix-helm -n "$NS" \
  -f "$REPO/deploy/phoenix-values.yaml" --set-string "database.url=$PG/phoenix" \
  ${PH_VER:+--version "$PH_VER"} --wait --timeout 10m

PH_SVC="$(kubectl get svc -n "$NS" -l app.kubernetes.io/instance=phoenix \
  -o jsonpath='{range .items[*]}{.metadata.name}{" "}{range .spec.ports[*]}{.port}{","}{end}{"\n"}{end}' \
  | grep 4317 | head -1 | cut -d' ' -f1)"
[ -n "$PH_SVC" ] || { echo "no Phoenix service exposes 4317" >&2; exit 1; }

helm upgrade --install aurai-observability "$REPO/charts/aurai-observability" -n "$NS" \
  -f "$REPO/charts/aurai-observability/values-local.yaml" \
  --set fanout.enabled=true --set-string "fanout.phoenixEndpoint=$PH_SVC.$NS.svc.cluster.local:4317" \
  --wait --timeout 5m

if [ -d "$INFRA/charts/aurai-platform" ]; then
  helm upgrade aurai-platform "$INFRA/charts/aurai-platform" -n "$INFRA_NS" --reuse-values \
    -f "$REPO/deploy/infra-collector-forward.values.yaml" --wait --timeout 5m
else
  echo "aurai-infra not found at $INFRA; apply deploy/infra-collector-forward.values.yaml yourself" >&2
fi
echo "Done. kubectl port-forward -n $NS svc/langfuse-web 3100:3000"
