#!/usr/bin/env bash
# Trace one SAGA across all 4 services with CloudWatch Logs Insights (SPEC §8, S6 gate).
#   scripts/trace.sh <env> <saga-id>       every log line of that saga, oldest first
#   scripts/trace.sh <env> --failed        the latest compensated/failed sagas and their correlation_id
# Needs operator credentials (e.g. AWS_PROFILE=aegisvolt). Looks back 24 h.
set -euo pipefail
env=${1:?usage: trace.sh <env> <saga-id>|--failed}
what=${2:?usage: trace.sh <env> <saga-id>|--failed}
groups=(/aegisvolt/"$env"/{station-reservation-service,billing-wallet-service,energy-dispatch-service,graphql-gateway})

if [[ $what == --failed ]]; then
  query='fields @timestamp, correlation_id, event, msg
| filter event in ["saga.compensated", "saga.compensation_failed", "saga.step.failed"]
| sort @timestamp desc | limit 20'
else
  [[ $what =~ ^[0-9a-f-]{36}$ ]] || { echo "not a saga id: $what" >&2; exit 2; }
  query="fields @timestamp, service, version, image_tag, level, event, msg
| filter correlation_id = \"$what\"
| sort @timestamp asc | limit 200"
fi

now=$(date +%s)
id=$(aws logs start-query --region us-east-1 --log-group-names "${groups[@]}" \
  --start-time $((now - 86400)) --end-time "$now" --query-string "$query" --query queryId --output text)
for _ in $(seq 60); do
  out=$(aws logs get-query-results --region us-east-1 --query-id "$id" --output json)
  status=$(jq -r .status <<<"$out")
  [[ $status == Complete || $status == Failed || $status == Cancelled ]] && break
  sleep 1
done
echo "Logs Insights query $id: $status" >&2
jq -r '.results[] | map({(.field): .value}) | add
  | [.["@timestamp"], (.service // .correlation_id), (if .version then "\(.version):\(.image_tag)" else "" end),
     .level // "", .event, .msg] | @tsv' <<<"$out" | column -t -s $'\t'
