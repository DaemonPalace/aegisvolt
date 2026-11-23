#!/usr/bin/env bash
# canary.sh <env> <svc> <tag> — canary rollout for one service, run on the env's node over SSM (SPEC §10).
# 1 canary pod next to 4 stable = 20% of Service traffic. Promote only if the canary is Ready with 0 restarts,
# its smoke-loop error rate is < 5% with at least one hit, and CanaryErrors (CloudWatch) is 0. Otherwise roll back, exit 1.
set -euo pipefail
cd "$(dirname "$0")/.."
source scripts/ssm.sh
ENV=$1 SVC=$2 TAG=$3
{
  echo "set -uo pipefail"
  echo "ENV=$ENV SVC=$SVC TAG=$TAG IMAGE=ghcr.io/${IMAGE_OWNER:-daemonpalace}/aegisvolt-$SVC AWS_REGION=us-east-1"
  cat <<'REMOTE'
export AWS_REGION
k() { kubectl -n aegisvolt "$@"; }
# strategic merge: env list merges by name, so only IMAGE_TAG changes
retag() { k patch deploy "$1" -p "{\"spec\":{\"template\":{\"spec\":{\"containers\":[{\"name\":\"app\",\"image\":\"$IMAGE:$TAG\",\"env\":[{\"name\":\"IMAGE_TAG\",\"value\":\"$TAG\"}]}]}}}}"; }
rollback() { echo "ROLLBACK $SVC:$TAG — $1"; k scale deploy "$SVC-canary" --replicas=0; exit 1; }

# first deploy: deploy.sh already put this tag on stable
[[ $(k get deploy "$SVC-stable" -o jsonpath='{.spec.template.spec.containers[0].image}') == "$IMAGE:$TAG" ]] \
  && { echo "SKIP $SVC: stable already runs $TAG"; exit 0; }

retag "$SVC-canary" && k scale deploy "$SVC-canary" --replicas=1
k rollout status deploy "$SVC-canary" --timeout=120s || rollback "canary pod not Ready in 120s"

ip=$(k get svc "$SVC" -o jsonpath='{.spec.clusterIP}')
hits=0 errs=0 stable=0
for _ in $(seq 50); do
  read -r code ver < <(curl -s -o /dev/null --max-time 3 -w '%{http_code} %header{x-version}\n' "http://$ip/health")
  if [[ $ver == canary ]]; then hits=$((hits+1)); [[ $code == 200 ]] || errs=$((errs+1)); else stable=$((stable+1)); fi
done
echo "smoke: stable=$stable canary=$hits canary_errors=$errs"

sleep 60  # let canary logs reach the CanaryErrors metric filter
cw=$(aws cloudwatch get-metric-statistics --namespace "AegisVolt/$ENV" --metric-name CanaryErrors \
  --start-time "$(date -u -d '-3 min' +%FT%TZ)" --end-time "$(date -u +%FT%TZ)" --period 180 \
  --statistics Sum --query 'sum(Datapoints[].Sum)' --output text)
restarts=$(k get pods -l "app=$SVC,track=canary" -o jsonpath='{.items[0].status.containerStatuses[0].restartCount}')
echo "gate: restarts=$restarts CanaryErrors=$cw"

[[ $restarts == 0 ]] || rollback "canary restarted $restarts times"
(( hits > 0 )) || rollback "no smoke request reached the canary"
(( errs * 100 < hits * 5 )) || rollback "canary error rate $errs/$hits >= 5%"
[[ ${cw%.*} == 0 ]] || rollback "CanaryErrors=$cw"

retag "$SVC-stable" && k rollout status deploy "$SVC-stable" --timeout=180s || rollback "stable rollout failed"
k scale deploy "$SVC-canary" --replicas=0
echo "PROMOTED $SVC:$TAG"
REMOTE
} | ssm_run "$ENV" 600
