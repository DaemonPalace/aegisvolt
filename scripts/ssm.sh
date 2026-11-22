# Sourced by deploy.sh and canary.sh. Runs a bash script on the env's k3s node through SSM Run Command.
# Uses the ambient AWS credentials (AWS_PROFILE=aegisvolt locally, the OIDC role in CI).
set -euo pipefail
export AWS_REGION=us-east-1

node_id() {
  aws ec2 describe-instances \
    --filters "Name=tag:Name,Values=aegisvolt-node-$1" Name=instance-state-name,Values=running \
    --query 'Reservations[0].Instances[0].InstanceId' --output text
}

# ssm_run <env> <timeout-seconds>   (script on stdin). Prints the node's stdout/stderr when done, returns its exit code.
ssm_run() {
  local id cmd status b64
  id=$(node_id "$1")
  [[ $id == i-* ]] || { echo "no running node for env $1" >&2; return 1; }
  b64=$(base64 -w0)
  cmd=$(aws ssm send-command --instance-ids "$id" --document-name AWS-RunShellScript \
    --timeout-seconds 60 \
    --parameters "{\"commands\":[\"echo $b64 | base64 -d | bash\"],\"executionTimeout\":[\"$2\"]}" \
    --query Command.CommandId --output text)
  while :; do
    sleep 5
    status=$(aws ssm get-command-invocation --command-id "$cmd" --instance-id "$id" --query Status --output text 2>/dev/null || echo Pending)
    case $status in Pending|InProgress|Delayed) ;; *) break ;; esac
  done
  aws ssm get-command-invocation --command-id "$cmd" --instance-id "$id" \
    --query '[StandardOutputContent,StandardErrorContent]' --output text
  [[ $status == Success ]]
}
