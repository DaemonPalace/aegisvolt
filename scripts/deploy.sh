#!/usr/bin/env bash
# deploy.sh <env> <tag> — applies k8s/ on the env's node over SSM (SPEC §10).
# Stable keeps the tag it already runs; only the first deploy puts <tag> on stable directly.
# Later image changes reach stable through canary.sh. Canary Deployments are (re)applied at 0 replicas.
set -euo pipefail
cd "$(dirname "$0")/.."
source scripts/ssm.sh
ENV=$1 TAG=$2
{
  echo "set -euo pipefail"
  echo "export ENV=$ENV NEW_TAG=$TAG REGISTRY=ghcr.io/${IMAGE_OWNER:-daemonpalace} CORS_ORIGINS='${CORS_ORIGINS:-}'"
  echo "TEMPLATE=\$(echo $(base64 -w0 k8s/deployment.yaml) | base64 -d)"
  echo "kubectl apply -f <(echo $(base64 -w0 k8s/services.yaml) | base64 -d)"
  cat <<'REMOTE'
k() { kubectl -n aegisvolt "$@"; }
for row in gateway:graphql-gateway:sagas reservation:station-reservation-service:reservations \
           billing:billing-wallet-service:billing dispatch:energy-dispatch-service:dispatch; do
  IFS=: read -r SVC SERVICE_NAME T <<<"$row"
  export SVC SERVICE_NAME TABLE_NAME=aegisvolt-$T-$ENV IMAGE=$REGISTRY/aegisvolt-$SVC
  cur=$(k get deploy "$SVC-stable" -o jsonpath='{.spec.template.spec.containers[0].image}' 2>/dev/null || true)
  stable_tag=${cur##*:}
  TRACK=stable REPLICAS=4 TAG=${stable_tag:-$NEW_TAG} envsubst <<<"$TEMPLATE" | k apply -f -
  TRACK=canary REPLICAS=0 TAG=$NEW_TAG envsubst <<<"$TEMPLATE" | k apply -f -
done
k wait --for=condition=Available deploy -l track=stable --timeout=300s
k get deploy -o wide
REMOTE
} | ssm_run "$ENV" 600
