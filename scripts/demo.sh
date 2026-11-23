#!/usr/bin/env bash
# Live answers to the exam's §6 question bank, one section per question (DEFENSE.md says what to say).
#   scripts/demo.sh envs        write prefect/envs.json from terraform output (all 3 workspaces)
#   scripts/demo.sh iac         6.1  workspaces isolate state + tables; tfstate versions in S3; packet path resources
#   scripts/demo.sh cicd        6.2  direct/force push to test is rejected; dynamic env in deploy.yml; canary gate
#   scripts/demo.sh saga [env]  6.3  fault flag on → startCharge → compensation in DynamoDB → idempotent replay (default test)
#   scripts/demo.sh obs         6.4  Prefect runs + artifacts; CloudWatch trace of the failed saga
#   scripts/demo.sh flags       6.4  flip a flag in dev without a redeploy; prod stays as it was
#   scripts/demo.sh all
# Needs: AWS_PROFILE=aegisvolt (operator), terraform, gh, jq, curl; PREFECT_API_URL for obs.
set -euo pipefail
cd "$(dirname "$0")/.."
export AWS_PROFILE=${AWS_PROFILE:-aegisvolt} AWS_REGION=us-east-1
ACCOUNT=272973566676
BUCKET=aegisvolt-tfstate-$ACCOUNT
STATE_KEY=aegisvolt/terraform.tfstate

h() { printf '\n\033[1m== %s\033[0m\n' "$*"; }
run() { printf '\033[2m$ %s\033[0m\n' "$*"; "$@"; }

# terraform output for one workspace, restoring the workspace that was selected before
tf_out() {  # tf_out <env> <name>
  local cur; cur=$(terraform -chdir=infra workspace show)
  terraform -chdir=infra workspace select "$1" >/dev/null
  terraform -chdir=infra output -raw "$2" 2>/dev/null || true
  terraform -chdir=infra workspace select "$cur" >/dev/null
}
api() { tf_out "$1" api_url; }
gql() {  # gql <env> <query> [admin]
  local hdr=(-H 'content-type: application/json')
  [[ ${3:-} == admin ]] && hdr+=(-H "X-Admin-Token: $(tf_out "$1" admin_token)")
  curl -sf -m 30 "${hdr[@]}" "$(api "$1")" -d "$(jq -nc --arg q "$2" '{query: $q}')"
}

envs() {
  h "prefect/envs.json from terraform output"
  local out='{"local": {"api_url": "http://localhost:8080/graphql", "suffix": "local", "dynamodb_endpoint": "http://localhost:8000"}}'
  for e in dev test prod; do
    u=$(api "$e"); out=$(jq --arg e "$e" --arg u "$u" '.[$e] = {api_url: ($u | select(. != "") // null), suffix: $e, region: "us-east-1"}' <<<"$out")
  done
  jq . <<<"$out" | tee prefect/envs.json
}

iac() {
  h "6.1a One state object per workspace (env:/<ws>/...) — an apply in test cannot touch prod's state"
  run terraform -chdir=infra workspace list
  run aws s3api list-objects-v2 --bucket "$BUCKET" --prefix env: --query 'Contents[].[Key,LastModified]' --output table
  h "6.1a Names carry the workspace: the tables of each env are different resources"
  run aws dynamodb list-tables --query 'TableNames[?starts_with(@, `aegisvolt-`)]' --output table
  h "6.1a A workspace outside dev/test/prod fails the plan (guard in infra/main.tf)"
  grep -n 'precondition' -A3 infra/main.tf | head -6
  h "6.1b Versions of dev's tfstate (bucket versioning on)"
  run aws s3api get-bucket-versioning --bucket "$BUCKET"
  run aws s3api list-object-versions --bucket "$BUCKET" --prefix "env:/dev/$STATE_KEY" \
    --query 'Versions[:5].[VersionId,LastModified,IsLatest,Size]' --output table
  echo "Restore a version: aws s3api copy-object --bucket $BUCKET --key env:/dev/$STATE_KEY \\"
  echo "  --copy-source \"$BUCKET/env:/dev/$STATE_KEY?versionId=<id>\"   (then terraform plan shows the drift)"
  h "6.1c Packet path: API Gateway → VPC Link (PrivateLink endpoint service on the NLB) → node → pod"
  run aws apigateway get-vpc-links --query 'items[?starts_with(name, `aegisvolt-`)].[name,status,targetArns[0]]' --output table
  run aws ec2 describe-vpc-endpoint-service-configurations \
    --query 'ServiceConfigurations[].[ServiceName,ServiceState,NetworkLoadBalancerArns[0]]' --output table
  run aws ec2 describe-vpc-endpoints --filters Name=tag:Project,Values=aegisvolt \
    --query 'VpcEndpoints[].[Tags[?Key==`Env`]|[0].Value,ServiceName,VpcEndpointType,State]' --output table
  run aws ec2 describe-security-groups --filters Name=group-name,Values='aegisvolt-*-node' \
    --query 'SecurityGroups[].[GroupName,IpPermissions[0].FromPort,IpPermissions[0].IpRanges[].CidrIp|join(`,`,@)]' --output table
}

cicd() {
  h "6.2a Branch protection on test (no force push, PR only, 6 required checks)"
  run gh api repos/DaemonPalace/aegisvolt/branches/test/protection \
    --jq '{force_push: .allow_force_pushes.enabled, deletions: .allow_deletions.enabled, admins: .enforce_admins.enabled, checks: .required_status_checks.contexts}'
  h "6.2a A direct push and a force push to test are rejected by GitHub"
  git fetch -q origin test
  local probe; probe=$(git commit-tree -p origin/test -m "demo: this push must be rejected" "origin/test^{tree}")
  run git push origin "$probe:refs/heads/test" 2>&1 | tail -4 || true
  run git push --force origin "$probe:refs/heads/test" 2>&1 | tail -4 || true
  h "6.2b deploy.yml: the branch that received the merge is the env (workspace select \$ENV, then apply)"
  grep -nE 'branches: \[dev, test, prod\]|ENV: \$\{\{ github.ref_name|workspace select|apply -auto-approve' .github/workflows/deploy.yml
  h "6.2c canary.sh: what is checked before 20% → 100%"
  sed -n '1,6p' scripts/canary.sh
  grep -nE 'restarts=|CanaryErrors|errs \* 100|rollback "|PROMOTED' scripts/canary.sh
}

saga() {
  local env=${1:-test}
  h "6.3 Fault flag ON in $env, then a startCharge mutation"
  gql "$env" 'mutation { setFlag(name: "SIMULATE_CHARGER_HARDWARE_FAULT", enabled: true) { name enabled } }' admin | jq -c .
  local id
  id=$(gql "$env" 'mutation { startCharge(input: {fleetId: "FLEET-ANDINA", stationId: "MDE-01", connectorId: "MDE-01-C2", kwh: 8}) { id status } }' | jq -r .data.startCharge.id)
  echo "saga $id"
  for _ in $(seq 40); do
    s=$(gql "$env" "{ saga(id: \"$id\") { status } }" | jq -r .data.saga.status)
    [[ $s != STARTED ]] && break; sleep 1
  done
  gql "$env" 'mutation { setFlag(name: "SIMULATE_CHARGER_HARDWARE_FAULT", enabled: false) { name enabled } }' admin | jq -c .
  h "6.3 The saga row: forward steps, then compensations in reverse order (charge → reserve), then the fault count"
  gql "$env" "{ saga(id: \"$id\") { status amountCents steps { name status detail } } }" | jq .data.saga
  h "6.3 DynamoDB, billing table: CHARGE and REFUND for this saga (net 0)"
  run aws dynamodb query --table-name "aegisvolt-billing-$env" --key-condition-expression 'pk = :p AND begins_with(sk, :s)' \
    --expression-attribute-values "{\":p\":{\"S\":\"FLEET#FLEET-ANDINA\"},\":s\":{\"S\":\"TXN#$id\"}}" \
    --query 'Items[].[sk.S,kind.S,amount_cents.N]' --output table
  h "6.3 DynamoDB, reservations table: RESERVE + RELEASE markers, and the connector"
  run aws dynamodb query --table-name "aegisvolt-reservations-$env" --key-condition-expression 'pk = :p' \
    --expression-attribute-values "{\":p\":{\"S\":\"SAGA#$id\"}}" --query 'Items[].[sk.S,at.S]' --output table
  run aws dynamodb get-item --table-name "aegisvolt-reservations-$env" \
    --key '{"pk":{"S":"STATION#MDE-01"},"sk":{"S":"CONNECTOR#MDE-01-C2"}}' --query 'Item.[status.S,fault_count.N]' --output text
  echo "(released by the compensation: AVAILABLE, fault 1/3; it closes OUT_OF_SERVICE at 3 in a row or if the charger is unreachable)"
  h "6.3 Idempotency: the same idempotencyKey twice → one saga, one CHARGE"
  local key; key=$(cat /proc/sys/kernel/random/uuid)
  for _ in 1 2; do
    gql "$env" "mutation { startCharge(input: {fleetId: \"FLEET-ANDINA\", stationId: \"CDMX-01\", connectorId: \"CDMX-01-C2\", kwh: 1, idempotencyKey: \"$key\"}) { id status } }" | jq -c .data.startCharge
  done
  sleep 6
  run aws dynamodb query --table-name "aegisvolt-billing-$env" --key-condition-expression 'pk = :p AND begins_with(sk, :s)' \
    --expression-attribute-values "{\":p\":{\"S\":\"FLEET#FLEET-ANDINA\"},\":s\":{\"S\":\"TXN#$key\"}}" --query 'Count'
  echo "Every participant writes a (sagaId, step) marker with attribute_not_exists: a replayed charge/refund/release"
  echo "hits the condition and returns the first result (services/*/app.py, test_participants_are_idempotent_and_tombstoned)."
  h "6.3 Reset the fault count (station owner: maintenance → reopen)"
  for a in maintenance reopen; do
    gql "$env" "mutation { connectorLifecycle(stationId: \"MDE-01\", connectorId: \"MDE-01-C2\", action: \"$a\", note: \"demo\") { id } }" admin >/dev/null
  done
  echo "MDE-01-C2 is AVAILABLE with fault count 0"
  echo "$id" > /tmp/aegisvolt-last-failed-saga
}

obs() {
  h "6.4 Prefect: latest env_health and saga_audit runs with their artifacts"
  [[ -n ${PREFECT_API_URL:-} ]] || export PREFECT_API_URL=http://localhost:4200/api
  run prefect/.venv/bin/python scripts/prefect_report.py 1
  h "6.4 CloudWatch Logs Insights: failed sagas, then the full trace of one by correlation_id"
  run scripts/trace.sh test --failed || run scripts/trace.sh dev --failed
  local id; id=$(cat /tmp/aegisvolt-last-failed-saga 2>/dev/null || true)
  if [[ -n $id ]]; then run scripts/trace.sh test "$id"; else echo "run 'demo.sh saga' first for a fresh failed saga"; fi
}

flags() {
  h "6.4 Flags before: dev vs prod"
  for e in dev prod; do echo "$e: $(gql "$e" '{ flags { name enabled } }' | jq -c '[.data.flags[] | {(.name): .enabled}] | add')"; done
  h "6.4 Flip ENABLE_FAST_CHARGE_BOOST in dev only (no redeploy: services read the flag on every request)"
  local was; was=$(gql dev '{ flags { name enabled } }' | jq '.data.flags[] | select(.name=="ENABLE_FAST_CHARGE_BOOST") | .enabled')
  gql dev "mutation { setFlag(name: \"ENABLE_FAST_CHARGE_BOOST\", enabled: $([[ $was == true ]] && echo false || echo true)) { name enabled } }" admin | jq -c .
  for e in dev prod; do echo "$e: $(gql "$e" '{ flags { name enabled } }' | jq -c '[.data.flags[] | {(.name): .enabled}] | add')"; done
  echo "Now reload the dev web app: UltraFast connectors lock/unlock on the next 2 s poll; prod's UI is unchanged."
  echo "Web: dev=$(tf_out dev web_url)  prod=$(tf_out prod web_url)"
  read -r -p "Press Enter to restore the flag… " _ || true
  gql dev "mutation { setFlag(name: \"ENABLE_FAST_CHARGE_BOOST\", enabled: $was) { name enabled } }" admin | jq -c .
}

case ${1:-all} in
  envs) envs ;; iac) iac ;; cicd) cicd ;; saga) saga "${2:-test}" ;; obs) obs ;; flags) flags ;;
  all) iac; cicd; saga test; obs; flags ;;
  *) sed -n '2,10p' "$0"; exit 2 ;;
esac
