# AegisVolt: defense guide

One answer per question in the exam's question bank (§6). Line references `src:NN` point to
`../ExamenFinal/Proyecto Final - Patrones Arquitectónicos Avanzados (AegisVolt).md`.
Every answer has a live command in `scripts/demo.sh <section>`. Run it with `AWS_PROFILE=aegisvolt`.

| Env | Web | API |
|---|---|---|
| dev | `terraform -chdir=infra workspace select dev && terraform -chdir=infra output web_url` | `… output api_url` |
| test | same, workspace `test` | same |
| prod | same, workspace `prod` | same |

Before the defense:
1. `scripts/demo.sh envs` writes `prefect/envs.json` for the 3 envs.
2. Start Prefect: `docker compose --profile observability up -d prefect-server`, then `prefect/.venv/bin/python prefect/flows.py serve` (see `prefect/README.md`).
3. Put every env back to the seed: `scripts/seed_local.py <env> <env> --reset`.

---

## 6.1 Infrastructure as code, S3 and networking (30%)

### "How does an apply in `test` avoid overwriting prod's DynamoDB tables or prod's state in S3?" (src:102–103)
`scripts/demo.sh iac`
- **Separate state.** Each env is its own Terraform **workspace**. With the S3 backend, the state of workspace `X` is the object `env:/X/aegisvolt/terraform.tfstate`. An apply in `test` only locks (`use_lockfile`) and writes `env:/test/…`. Prod's state object is never opened.
- **Separate resources.** Every name is built from `terraform.workspace`: `aegisvolt-billing-test`, `aegisvolt-nlb-test`, `/aegisvolt/test/*`, VPC 10.20.0.0/16. The test state only knows test's resources, so it cannot plan a change to `aegisvolt-billing-prod`.
- **Guard.** `infra/main.tf` has a `precondition`: any workspace outside `dev|test|prod` (`default` included) fails the plan.
- **CI.** The pipeline never chooses the workspace by hand. It is the branch name: `ENV: ${{ github.ref_name }}` → `terraform workspace select "$ENV"`.

### "Show the previous versions of dev's tfstate in S3 and how you would restore one" (src:100)
`scripts/demo.sh iac` (section 6.1b) lists the versions.
- **Why versions exist.** The bucket `aegisvolt-tfstate-272973566676` has versioning **Enabled** (bootstrap), so every apply keeps the previous state as a non-current version.
- **How to restore:**
  1. Copy the good version on top of the current object. This creates a new latest version; nothing is deleted.
     ```sh
     aws s3api copy-object --bucket aegisvolt-tfstate-272973566676 \
       --key env:/dev/aegisvolt/terraform.tfstate \
       --copy-source "aegisvolt-tfstate-272973566676/env:/dev/aegisvolt/terraform.tfstate?versionId=<id>"
     ```
  2. Run `terraform plan` in dev. It shows any drift between the restored state and the real resources.
  3. Fix the drift with `apply` or `terraform import`.

### "Trace a packet from API Gateway to the Kubernetes pod through the VPCE. Which route table or security rule applies?" (src:104–106)
`scripts/demo.sh iac` (section 6.1c) shows each piece.
1. **Client → API Gateway.** HTTPS to the regional REST API `aegisvolt-api-<env>` (stage `<env>`, route `ANY /graphql`).
2. **API Gateway → VPC.** This is the "VPC Endpoint Privado" in the diagram. The integration is `HTTP_PROXY` with `connection_type = VPC_LINK`.
   - The **REST VPC Link** `aegisvolt-vpclink-<env>` is PrivateLink. AWS creates a **VPC endpoint service** on our internal NLB (`describe-vpc-endpoint-service-configurations` shows it as `Available`), and API Gateway connects to it as the consumer.
   - The NLB sits in the **private subnets**. The packet never crosses the internet.
3. **NLB → node.** The private route table's `local` route (the VPC CIDR) carries it. **Security group `aegisvolt-<env>-node`** allows TCP **30080 only from the private subnet CIDRs**, where the NLB lives. There is no SSH; we administer the node through SSM.
4. **Node → pod.** NodePort 30080 → kube-proxy (iptables) → `graphql-gateway` pod. The gateway calls the 3 services by ClusterIP.
5. **Pod → DynamoDB / S3 / CloudWatch.** These use the **gateway VPCEs** for DynamoDB and S3 (prefix-list routes in the private route table) and the **interface VPCE** for `logs` (private DNS, SG `aegisvolt-<env>-vpce`, 443 from the VPC). None of this goes through the NAT or the internet.

**Why a VPC Link instead of an interface VPCE for API Gateway:**
- An interface VPCE is the way *out* of the VPC to an AWS service: a caller inside the VPC reaching a private API.
- API Gateway → our cluster goes the other way, from outside the VPC *in*. AWS's mechanism for that direction is the VPC Link, which is itself a PrivateLink endpoint connection.

---

## 6.2 CI/CD, promotion and canary deployments (25%)

### "Try a force push or a direct push to `test` and show the rule that blocks it" (src:123–124)
`scripts/demo.sh cicd`
- **The rules.** `scripts/protect-branches.sh` set branch protection on `dev`, `test` and `prod`:
  - a PR is required;
  - `allow_force_pushes=false` and `allow_deletions=false`;
  - `enforce_admins=true`, so not even the owner can bypass it;
  - 6 required checks in strict mode (`promotion-guard`, `lint-build`, `unit-tests`, `integration-tests`, `e2e-tests`, `terraform-plan`).
- **The proof.** The script builds a commit on top of `origin/test` and pushes it twice, plain and with `--force`. GitHub answers `GH006: Protected branch update failed` both times.
- **Promotion order.** `promotion-guard` enforces `feature/*|fix/* → dev`, `dev → test`, `test → prod` (src:127–147). A `feature/x → test` PR fails it.

### "Show in the pipeline YAML how it decides which env to `terraform apply` after a merge" (src:158)
`.github/workflows/deploy.yml`
- **Trigger.** `on: push: branches: [dev, test, prod]`. A merged PR is a push to its base branch.
- **Env.** `ENV: ${{ github.ref_name }}`: the branch that received the merge is the env.
- **Apply.** `terraform workspace select "$ENV"` then `terraform apply -auto-approve`, which changes only that workspace.
- **No overlap.** `concurrency: deploy-${{ github.ref_name }}` serializes deploys of the same env and lets different envs run side by side.
- **Before the merge.** In the PR, `terraform-plan` selects `${{ github.base_ref }}`, the target branch, and posts the plan (src:156).

### "What metric or condition does the canary stage check before going from 20% to 100%?" (src:159)
`scripts/canary.sh`, which runs on the node through SSM.
- **The 20%.** Each service has `<svc>-stable` (4 replicas) and `<svc>-canary` (1 replica) behind one Service, so 1 out of 5 pods is about **20% of the traffic**.
- **Promote only if all of these hold:**
  - the canary pod is Ready, with 0 restarts;
  - the smoke loop (50 × `/health`, each response counted by its `X-Version` header) gives the canary an error rate **< 5%**, with at least one hit;
  - the CloudWatch metric **`CanaryErrors` = 0** over the last 3 minutes. That metric comes from a filter on `{ $.level = "ERROR" && $.version = "canary" }`.
- **If all hold:** stable moves to the new tag and the canary scales to 0. **Otherwise:** automatic rollback (canary to 0, stable untouched), and the job goes red.

---

## 6.3 Architecture: SAGA and feature flags (25%)

### "Turn on the fault flag in `test`, fire a charge mutation and show the compensation in DynamoDB" (src:89–94)
`scripts/demo.sh saga test`, or use the test web app with the flag panel.
1. `setFlag(SIMULATE_CHARGER_HARDWARE_FAULT, true)` in test. Flags live in `aegisvolt-flags-test` and are read on every request.
2. `startCharge` → the orchestrator in the gateway runs: `reserve` OK → `charge` OK → `activate` **FAILED** (dispatch answers 503 `CHARGER_HARDWARE_FAULT`).
3. **Compensation in reverse order**, as src:93 requires:
   - `charge` COMPENSATED: billing `/refund`;
   - then `reserve` COMPENSATED: reservation `/release`, which puts the connector back to **`AVAILABLE`**.

   The saga ends `COMPENSATED`.
4. What DynamoDB shows:
   - `aegisvolt-billing-test`: `TXN#<saga>#CHARGE` and `TXN#<saga>#REFUND` for the same amount, so the net is 0.
   - `aegisvolt-reservations-test`: the markers `SAGA#<saga>/RESERVE` and `/RELEASE`.
   - `aegisvolt-sagas-test`: the full step trail.
5. **After** the compensation, an extra step `fault` takes the faulty connector `OUT_OF_SERVICE`. This is a station-owner rule we added: a charger that reported a hardware fault must not be booked again. It is not part of the compensation. The owner sends it to maintenance and reopens it from the Station owner tab (the script does it at the end).
6. **Insufficient funds** (src:94): `FLEET-LOWFUNDS` (5 USD) → `charge` 402 → only `reserve` is compensated → the connector is released immediately.

### "How does your SAGA avoid idempotency problems if a message or compensation request is processed twice?" (src:205)
- **Markers.** Every step writes a marker keyed by `(sagaId, step)` with `attribute_not_exists` inside the same `TransactWriteItems` as the business change:
  - `/charge` = wallet update (`balance >= amount`) + `Put TXN#<saga>#CHARGE`;
  - `/refund` = `ConditionCheck` that CHARGE exists + `Put TXN#<saga>#REFUND` + wallet update;
  - `/reserve` / `/release` = `SAGA#<id>/RESERVE|RELEASE` markers + a conditional update on the connector (`saga_id = :sid`).
- **A duplicate.** It fails the marker condition, the transaction is cancelled as a whole, and the service answers with the **stored first result**. Money and connectors are not touched twice.
- **Tombstones.** A `release` (or `stop`) that arrives *before* its forward call leaves a marker, so the late `reserve` is refused (410) and cannot orphan the connector.
- **Client side.** `startCharge` takes an `idempotencyKey`. The same key returns the same saga and starts nothing. The UI sends one per click.
- **Retries.** Compensations are retried with backoff 1/2/4/8/16 s. That is safe because of the markers. If they still fail, the saga ends `COMPENSATION_FAILED` and the Prefect audit flags it as an orphan.
- **Tests:** `test_participants_are_idempotent_and_tombstoned` and `test_hardware_fault_…_replay_changes_nothing` (`services/test_unit.py`), plus `test_idempotent_replay_starts_one_saga_and_charges_once` (e2e).

---

## 6.4 Observability and presentation (20%)

### "Show the observability flow in Prefect and trace a failed saga's correlation_id in CloudWatch" (src:112–113, 209)
`scripts/demo.sh obs`
- **Prefect** (`prefect/flows.py`, a `*/5` cron each). The UI is http://localhost:4200.
  - `env_health`: GraphQL `health` plus the 5 tables, per env.
  - `saga_audit`: cross-checks the sagas, billing and reservations tables. It flags **orphans** (STARTED > 5 min, COMPENSATION_FAILED, or a charge that never finished) and **inconsistencies** (compensated without a refund, a connector still held, …). It publishes markdown and table artifacts, and **the run fails** if it finds any.
- **CloudWatch.**
  - Every service logs JSON with `env`, `service`, `version` and **`correlation_id` = sagaId**. The gateway sends it downstream as `X-Correlation-ID`.
  - The groups are `/aegisvolt/<env>/<service>`.
  - `scripts/trace.sh <env> --failed` lists the failed sagas. `scripts/trace.sh <env> <id>` prints that saga from all 4 services in order: reserve → charge → `dispatch.fault` → compensating → refund → release.
  - The same queries are saved in the console under Logs Insights: `aegisvolt-<env>-failed-sagas` and `aegisvolt-<env>-by-correlation`.

### "Change a flag in `dev` without redeploying and show the dev frontend change while prod stays the same" (src:210)
`scripts/demo.sh flags`
- **Why no redeploy.** Flags are rows in `aegisvolt-flags-<env>`, one table per env. The services read them on **every request** (no cache), and Terraform seeds them with `ignore_changes`, so a runtime toggle survives later applies.
- **The demo.**
  1. Toggle `ENABLE_FAST_CHARGE_BOOST` in dev (`setFlag`, which needs the admin token).
  2. The dev web app polls every 2 s: UltraFast connectors switch between locked and available, and the flag panel changes.
  3. The prod web app keeps showing prod's own table, so nothing changes there.
- **Defaults per env:**

  | Flag | dev | test | prod |
  |---|---|---|---|
  | surge pricing | on | on | off |
  | fast-charge boost | on | off | off |
  | fault simulation | off | off | off |

### Frontend (8%)
Next.js static export on one S3 website per env. `config.json`, written by the deploy, points the build at that env's API.
- **Charge tab:** station → connector (UltraFast marked) → kWh with a live price (rate × kWh × surge). Then a live pop-up per charge, with a countdown until it is done or compensated.
- **Transactions tab:** every saga expands to its orchestration flow and the money it moved.
- **Station owner tab:** fault → maintenance → reopen, add/remove connectors, energy per session, connector, station and fleet.

---

## Deliberate simplifications (if asked)
- **Single-node k3s instead of EKS**, because of cost and the Free plan. It is the same Kubernetes API, and the module can be swapped for `terraform-aws-modules/eks`.
- **Surge applies all day** (a "simulated peak").
- **Charging runs ×10 real time** (`CHARGE_SPEEDUP`), so the demo finishes in seconds.
- **The flags are our own DynamoDB table** (src:80 allows it). No Unleash/AppConfig.
