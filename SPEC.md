# AegisVolt — Technical Specification

Source requirements: `../ExamenFinal/Proyecto Final - Patrones Arquitectónicos Avanzados (AegisVolt).md`.
Plan: stages S0–S7. Each stage ends with a manual review gate. Deadline: 2026-11-23 17:00.

**Progress** (update at every gate):
- S0 Bootstrap: done (PR #1)
- S1 Infra: done, dev applied (PR #2). Node `i-0ac341dfa40ce9367`, api_url `https://c2sp5wscqc.execute-api.us-east-1.amazonaws.com/dev/graphql`
- S2 Services: done (PR #3). Local stack: `docker compose up -d --build --wait`; tests: `.venv/bin/pytest` (venv from `requirements-dev.txt`, Python 3.12)
- S3 K8s/Canary: done (PR #5). Images: public `ghcr.io/daemonpalace/aegisvolt-<svc>:<sha7>`; `scripts/{build-push,deploy,canary}.sh`
- S4 CI/CD: done (PR #6). 6 required checks (strict) on dev/test/prod; push to an env branch deploys that env
- S5 Frontend: in review. Next.js static export on an S3 website per env (`web_url` output)
- UX redesign: done (PR #11). Tabs Charge / Transactions / Station owner. Charges end FINISHED at `endsAt`. Connectors go OUT_OF_SERVICE after 3 consecutive hardware faults (or at once if the charger is unreachable), then MAINTENANCE. Deposits and new fleets. `seed_local.py --reset`
- S6 Observability: done (PR #12). `scripts/trace.sh` and saved Logs Insights queries; `prefect/flows.py` (`env_health`, `saga_audit`) on a local Prefect server (compose profile `observability`)
- **S7 Integration: in progress.** `dev → test` merged (PR #13). `DEFENSE.md` and `scripts/demo.sh` added. Next: `test → prod`, then `demo.sh envs`

## 0. Conventions

- **Environments**: `dev`, `test`, `prod`. Each env has a Terraform workspace, a Git branch, a VPC, a k3s node, an API Gateway, tables and log groups of the same name.
- **Naming**: `aegisvolt-<resource>-${terraform.workspace}` (example: `aegisvolt-billing-test`). Log groups: `/aegisvolt/<env>/<service>`.
- **Region**: `us-east-1`. **Terraform** ≥ 1.10. **Python** 3.12 (container base `python:3.12-slim`). **Node** 22 (frontend build only: Next.js 16 static export).
- **Money**: integer cents (USD). **Time**: ISO-8601 UTC strings.
- **Correlation**: `sagaId` (UUIDv4) is the `correlation_id`. The gateway generates it and sends it to every downstream call as the `X-Correlation-ID` header. Every service logs it.
- **Reuse first**: services, frontend, tests and Prefect flows are ported from earlier course projects (mainly `../wandersync` and `../Taller-PatronSaga`). See §17 for each donor file and what changed.

## 1. Repository layout

```
aegisvolt/
├── SPEC.md  DEFENSE.md  README.md
├── bootstrap/                 # local-state TF: state bucket + 2 GitHub roles; reuses the existing OIDC provider (run once)
├── infra/                     # root TF module (main.tf: backend, envs map, workspace guard, outputs)
│   └── modules/{network,data,k3s,apigw,observability}/   # web hosting decided in S5
├── seed/seed.json             # stations, connectors, fleets, flag defaults (used by TF AND local seed)
├── services/
│   ├── common/                # web.py (app factory), log.py (JSON + watchtower), ddb.py (tables, flags, txn helpers)
│   ├── gateway/               # app.py + schema.py (Strawberry GraphQL) + saga.py (SAGA orchestrator) + schema.graphql (export)
│   ├── reservation/  billing/  dispatch/   # FastAPI
│   ├── Dockerfile             # one recipe, --build-arg SERVICE=<dir>
│   ├── test_unit.py           # unit tests: all services in-process on moto
│   └── requirements.txt       # single shared requirements file
├── k8s/                       # manifests rendered with envsubst
├── frontend/                  # Next.js 16 static export (output: "export")
├── prefect/flows.py
├── tests/{integration,e2e}/   # GraphQL tests against a running stack (API_URL)
├── scripts/                   # protect-branches.sh deploy.sh canary.sh seed_local.py prefect_report.py demo.sh
├── docker-compose.yml
└── .github/workflows/{pr.yml,deploy.yml}
```

## 2. Domain & seed data (`seed/seed.json`)

**Stations**: each station has 2 `FAST` connectors (150 kW) and 1 `ULTRA` connector (350 kW).

| id | name | city |
|---|---|---|
| `BOG-01` | Bogotá Norte | Bogotá |
| `MDE-01` | El Poblado | Medellín |
| `CDMX-01` | Vallejo | Ciudad de México |

Connector ids: `<station>-C1` and `<station>-C2` are FAST, `<station>-C3` is ULTRA.

**Fleets**:

| fleetId | initial balance (cents) | purpose |
|---|---|---|
| `FLEET-ANDINA` | 50000 | happy path |
| `FLEET-LOWFUNDS` | 500 | step-2 failure demo (use kwh ≥ 14: 20 kWh with surge = 750 cents) |

**Pricing**: base rate is 25 cents/kWh. Surge multiplier is ×1.5. `amount = ceil(kwh × 25 × multiplier)`.

## 3. Feature flags

There is one table per env: `aegisvolt-flags-<env>`. Terraform seeds the rows with `lifecycle { ignore_changes = [item] }`, so a toggle made at runtime survives the next `terraform apply`. Services read the flag on **every request** (no cache), so a toggle takes effect immediately without a redeploy.

| Flag | dev | test | prod | Read by | Effect |
|---|---|---|---|---|---|
| `ENABLE_DYNAMIC_SURGE_PRICING` | true | true | false | billing | multiplier ×1.5 on charge |
| `ENABLE_FAST_CHARGE_BOOST` | true | false | false | reservation | `ULTRA` connectors can be reserved only when the flag is on (otherwise 403) |
| `SIMULATE_CHARGER_HARDWARE_FAULT` | false | false | false | dispatch | `/activate` returns 503, which triggers compensation |

`ponytail:` the surge flag applies all day ("simulated peak"). Add a peak-hour window if the professor asks for one.

## 4. DynamoDB tables (all `PAY_PER_REQUEST`, keys `pk` (S) + `sk` (S) unless noted)

| Table (`-<env>`) | Owner | Items |
|---|---|---|
| `aegisvolt-reservations` | reservation | connector `pk=STATION#<id>`, `sk=CONNECTOR#<cid>` {type, power_kw, status `AVAILABLE`/`RESERVED`/`OUT_OF_SERVICE`/`MAINTENANCE`, saga_id, note, fault_count, last_fault_at, updated_at}; station meta `sk=META` {name, city}; idempotency marker `pk=SAGA#<sagaId>`, `sk=RESERVE`/`RELEASE`/`FAULT` {result} |
| `aegisvolt-billing` | billing | wallet `pk=FLEET#<id>`, `sk=WALLET` {balance_cents}; txn `pk=FLEET#<id>`, `sk=TXN#<sagaId>#CHARGE`/`TXN#<sagaId>#REFUND`/`TXN#TOPUP#<ts>` {amount_cents, surge, created_at} |
| `aegisvolt-dispatch` | dispatch | session `pk=SESSION#<sagaId>`, `sk=META` {status `ACTIVE`/`FAILED`/`STOPPED`, station_id, connector_id, kwh}; telemetry `pk=STATION#<id>`, `sk=TELEMETRY#<ts>` {saga_id, connector_id, power_kw, energy_kwh, voltage} |
| `aegisvolt-sagas` | gateway | `pk=SAGA#<id>`, `sk=META` {status, fleet_id, station_id, connector_id, kwh, amount_cents, created_at, updated_at, steps: list of {name, status, at, detail}} |
| `aegisvolt-flags` | gateway (write) / all (read) | key `name` (S) only {enabled (BOOL), description, updated_at} |

`ponytail:` the `sagas(limit)` query uses Scan and sorts in the app. Add a GSI on `created_at` if the row count grows past the demo scale.

## 5. Internal REST contracts (ClusterIP, JSON, header `X-Correlation-ID`)

All services expose `GET /health`, which returns `{status:"ok", service, env, version}`.

**reservation** (`station-reservation-service`)
| Method | Path | Body | Responses |
|---|---|---|---|
| GET | `/stations` | — | 200 `[station + connectors]` |
| POST | `/reserve` | `{saga_id, station_id, connector_id}` | 200 `{status:"RESERVED"}` · 409 not available · 403 ULTRA reserved while the boost flag is off |
| POST | `/release` | `{saga_id, station_id, connector_id}` | 200 `{status:"AVAILABLE"}` (idempotent; releases only if `saga_id` matches the holder) |
| POST | `/connectors/lifecycle` | `{station_id, connector_id, action, note?, saga_id?}` | `fault`/`unreachable` (SAGA, keyed by `saga_id`): 200 `{status, fault_count, threshold, replay}` · `healthy` (SAGA): resets the count · `maintenance`/`reopen` (owner): 200 `{status}` · 409 wrong source status |

**billing** (`billing-wallet-service`)
| Method | Path | Body | Responses |
|---|---|---|---|
| GET | `/wallets/{fleet_id}` | — | 200 `{balance_cents, transactions[]}` |
| POST | `/charge` | `{saga_id, fleet_id, kwh}` | 200 `{amount_cents, surge}` · 402 insufficient funds |
| POST | `/refund` | `{saga_id, fleet_id}` | 200 `{refunded_cents}` (idempotent; returns 0 when there is no CHARGE or a REFUND already exists) |
| POST | `/topup` | `{fleet_id, amount_cents}` | 200 wallet |

`/charge` is one `TransactWriteItems`:
- `Update` on the wallet: `balance -= amount`, with condition `balance >= amount`.
- `Put` of `TXN#<sagaId>#CHARGE`, with condition `attribute_not_exists(pk)`.

`/refund` is one `TransactWriteItems`:
- `ConditionCheck` that CHARGE exists.
- `Put` of the REFUND item, with condition `attribute_not_exists`.
- `Update` on the wallet: `balance += amount`.

A `TransactionCanceledException` caused by the duplicate-marker condition counts as an **idempotent replay**: the service returns the stored result.

**dispatch** (`energy-dispatch-service`)
| Method | Path | Body | Responses |
|---|---|---|---|
| POST | `/activate` | `{saga_id, station_id, connector_id, kwh}` | 200 `{status:"ACTIVE"}` · 503 `{error:"CHARGER_HARDWARE_FAULT"}` (the session is stored as `FAILED`) |
| POST | `/stop` | `{saga_id}` | 200 `{status:"STOPPED"}` |
| GET | `/telemetry/{station_id}` | — | 200 last 20 points. Each call first writes one simulated point per `ACTIVE` session on that station. |

## 6. SAGA — "Reserve & Activate Charge" (orchestration in gateway)

Orchestrator: `services/gateway/saga.py`, a plain-async port of wandersync's `services/orders-billing/saga.py` with the Prefect decorators removed. Charging never depends on Prefect being reachable; Prefect only observes (§13).

```
startCharge ─► sagas row STARTED
  1 reservation /reserve   ─fail─► status FAILED (nothing to compensate)
  2 billing /charge        ─fail─► C1 reservation /release ─► COMPENSATED
  3 dispatch /activate     ─fail─► C2 billing /refund ─► C1 reservation /release ─► COMPENSATED
  ok ─► COMPLETED
stopCharge(sagaId) ─► dispatch /stop ─► reservation /release ─► STOPPED
```

- **Saga statuses**: `STARTED`, `COMPLETED`, `FAILED`, `COMPENSATED`, `COMPENSATION_FAILED`, `STOPPED`.
- **Step statuses**: `OK`, `FAILED`, `COMPENSATED`. A compensation entry carries the *forward* step's name (`charge` + `COMPENSATED` = refund), so the compensation order reads directly from `steps`.
- Each step appends `{name, status, at, detail}` to `steps`, and the saga row is updated after every step. The UI history panel and Prefect audit read from this row.
- **Retry policy** (carried over from wandersync ADR-05 and ADR-06):
  - Forward steps are retried once on a 5xx or a transport error. A 4xx is a business rejection and is not retried. A rejected step did not happen, so only the steps before it are compensated (step 1 rejected → `FAILED`).
  - A timeout (`SAGA_CLIENT_TIMEOUT_SECONDS`) is treated as a failure, because the outcome is unknown, so **the timed-out step is compensated too** (activate → dispatch `/stop`). Compensations are idempotent and tombstone-safe (a release/stop that lands before its forward call makes the late forward call refuse: 410/409), so this is safe.
  - Compensations are retried up to 5 times, with backoff 1/2/4/8/16 s. If they are still failing, the status becomes `COMPENSATION_FAILED`, and Prefect flags the saga as an orphan.
- **Fault rule (station owner, on top of the SAGA)**: after the compensation of a failed `activate`, the gateway records a step `fault`.
  - Stage 1: dispatch answered 503 `CHARGER_HARDWARE_FAULT` → reservation `fault` increments `fault_count`; the connector stays `AVAILABLE`.
  - Stage 2: it closes `OUT_OF_SERVICE` at **3 consecutive** faults, or at once with `unreachable` (activate timed out or dispatch could not be reached).
  - Reset: `reopen` (MAINTENANCE → AVAILABLE) clears the count; so does a successful `activate` (reserve returns `fault_count`, and only when it is > 0 the gateway sends `healthy`, step `healthy`).
  - Idempotent: the count and the `SAGA#<id>/FAULT` marker are one `TransactWriteItems`, so a replay counts once.
- **Crash recovery**: at gateway startup, sagas still `STARTED` after 60 s are compensated (ported from wandersync's `orders-billing/app.py` recovery).
- **Idempotency**:
  - Every forward step and every compensation is keyed by `(sagaId, step)` through a DynamoDB conditional write, so a replayed request returns the first result and changes nothing.
  - `startCharge` also accepts an optional client `idempotencyKey`. If a saga row with that id already exists, the mutation returns it unchanged.
- **Log events**: `saga.started`, `saga.step.ok`, `saga.step.failed`, `saga.compensating`, `saga.compensated`, `saga.completed`, `saga.compensation_failed`.

## 7. GraphQL schema (gateway, `POST /graphql`)

```graphql
enum ConnectorType { FAST ULTRA }
enum ConnectorStatus { AVAILABLE RESERVED }
enum SagaStatus { STARTED COMPLETED FAILED COMPENSATED COMPENSATION_FAILED STOPPED }

type Connector { id: ID! stationId: ID! type: ConnectorType! powerKw: Int! status: ConnectorStatus! sagaId: ID }
type Station { id: ID! name: String! city: String! connectors: [Connector!]! }
type TelemetryPoint { stationId: ID! connectorId: ID! sagaId: ID! ts: String! powerKw: Float! energyKwh: Float! voltage: Float! }
type Flag { name: String! enabled: Boolean! description: String updatedAt: String }
type WalletTxn { sagaId: ID kind: String! amountCents: Int! createdAt: String! }
type Wallet { fleetId: ID! balanceCents: Int! transactions: [WalletTxn!]! }
type SagaStep { name: String! status: String! at: String! detail: String }
type Saga { id: ID! status: SagaStatus! fleetId: ID! stationId: ID! connectorId: ID! kwh: Float! amountCents: Int createdAt: String! steps: [SagaStep!]! }
type ServiceHealth { service: String! status: String! version: String! }
type Health { env: String! version: String! services: [ServiceHealth!]! }

input StartChargeInput { fleetId: ID! stationId: ID! connectorId: ID! kwh: Float! idempotencyKey: ID }

type Query {
  stations: [Station!]!
  telemetry(stationId: ID!): [TelemetryPoint!]!
  flags: [Flag!]!
  wallet(fleetId: ID!): Wallet!
  sagas(limit: Int = 20): [Saga!]!
  saga(id: ID!): Saga
  health: Health!
}

type Mutation {
  startCharge(input: StartChargeInput!): Saga!
  stopCharge(sagaId: ID!): Saga!
  setFlag(name: String!, enabled: Boolean!): Flag!      # requires X-Admin-Token
  topUp(fleetId: ID!, amountCents: Int!): Wallet!       # requires X-Admin-Token
}
```

- `setFlag` and `topUp` check the `X-Admin-Token` header against the `ADMIN_TOKEN` env var. Terraform generates a `random_password` per env and stores it as a k8s Secret.
- `kwh` must be in (0, 100]. `amountCents` must be in (0, 1 000 000]. Other values are rejected with a GraphQL error.
- CI exports the schema to `services/gateway/schema.graphql` (`strawberry export-schema`) and fails if the export differs from the committed file.

## 8. Service configuration (env vars)

| Var | Used by | Example |
|---|---|---|
| `ENV` | all | `dev` |
| `SERVICE_NAME` | all | `billing-wallet-service` |
| `VERSION` | all | `stable` / `canary` |
| `IMAGE_TAG` | all | git SHA |
| `AWS_REGION` | all | `us-east-1` |
| `DYNAMODB_ENDPOINT` | all (local only) | `http://dynamodb:8000` |
| `TABLE_NAME` | each service | `aegisvolt-billing-dev` |
| `FLAGS_TABLE` | all | `aegisvolt-flags-dev` |
| `SAGAS_TABLE` | gateway | `aegisvolt-sagas-dev` |
| `RESERVATION_URL`, `BILLING_URL`, `DISPATCH_URL` | gateway | `http://reservation` |
| `ADMIN_TOKEN` | gateway | secret |
| `CORS_ORIGINS` | gateway | web bucket URL |
| `CLOUDWATCH_LOGS` | all | `true` on AWS, `false` locally |
| `SAGA_STEP_DELAY_SECONDS` | gateway | `1.5` (so the UI shows each step; `0` in tests) |
| `SAGA_CLIENT_TIMEOUT_SECONDS` | gateway | `5` |

**App factory** (`services/common/web.py`, ported from wandersync's `make_app`):
- `/health` runs `describe_table` on the service's own table.
- A middleware reads `X-Correlation-ID` (or creates one) into a contextvar, and the logger adds it to every line.
- Every response carries an `X-Version: stable|canary` header (used by the canary gate, §10).

**Logging** (`services/common/log.py`, `python-json-logger`): one JSON line per event, written to stdout. When `CLOUDWATCH_LOGS=true`, lines also go through `watchtower` to group `/aegisvolt/<env>/<service>` with stream `<pod-name>`.

```json
{"ts":"…","level":"INFO","env":"dev","service":"billing-wallet-service","version":"stable","image_tag":"ab12cd3","correlation_id":"<sagaId>","event":"saga.step.ok","msg":"charged","amount_cents":1875}
```

## 9. Infrastructure (Terraform)

**State**:
- The bootstrap creates bucket `aegisvolt-tfstate-<account_id>` with versioning, SSE-S3, and a public-access block.
- Backend: `key = "aegisvolt/terraform.tfstate"`, `use_lockfile = true`, default `workspace_key_prefix`. Objects land at `env:/<ws>/aegisvolt/terraform.tfstate`.

**Workspace guard**: `infra/main.tf` holds a `locals.envs` map keyed by `dev`, `test`, `prod`. A `terraform_data` precondition fails the plan when `terraform.workspace` is not one of those keys, so `default` is rejected.

| ws | VPC CIDR | instance |
|---|---|---|
| dev | 10.10.0.0/16 | m7i-flex.large |
| test | 10.20.0.0/16 | m7i-flex.large |
| prod | 10.30.0.0/16 | m7i-flex.large |

**Modules**:
- **network**:
  - VPC with 2 public and 2 private subnets (2 AZs, because the NLB and the interface VPCE need them); compute runs only in AZ-a.
  - IGW, one NAT GW, and route tables.
  - Gateway VPCEs for S3 and DynamoDB, attached to the private route table.
  - Interface VPCE for `logs` (CloudWatch), with private DNS.
  - SGs:
    - `vpce-sg` (on the `logs` endpoint): ingress 443 from the VPC CIDR.
    - `node-sg`: ingress 30080 only from the private subnet CIDRs, where the NLB lives; no SSH.
- **data**: the 5 tables plus seed items from `seed/seed.json` and the per-env flag defaults.
- **k3s**:
  - EC2 `m7i-flex.large` (the account's Free plan only allows free-tier types) running AL2023 in the private subnet.
  - IMDSv2 required with `http_put_response_hop_limit = 2`, so pods can use the instance role.
  - `user_data` installs k3s and creates namespace `aegisvolt` and Secret `aegisvolt-secrets` (ADMIN_TOKEN).
  - Instance role with these policies: SSM managed core, DynamoDB on `aegisvolt-*-<env>`, CloudWatch Logs on `/aegisvolt/<env>/*`.
  - `ponytail:` single-node k3s instead of EKS (cost). Same Kubernetes API; swap this module for `terraform-aws-modules/eks` to get EKS.
- **apigw**: one API Gateway, exactly as in the diagram.
  - **Internal NLB** `aegisvolt-nlb-<env>` in the private subnets.
    - TCP listener on 80, forwarding to a target group with the node instance on port 30080.
    - TCP health check. `preserve_client_ip = false`, so the source IP the node sees is always the NLB's.
  - **REST VPC Link (v1)** `aegisvolt-vpclink-<env>` targeting the NLB.
    - It is built on PrivateLink: AWS creates a **VPC endpoint service** on the NLB, and API Gateway connects to it as the consumer (auto-approved).
    - That connection is the diagram's "VPC Endpoint Privado". Show it after S1 under VPC → Endpoint services.
    - AWS recommends v2 for new links, but v1 is still supported. We use v1 on purpose because the endpoint service is visible.
  - **REST API** `aegisvolt-api-<env>` (endpoint type `REGIONAL`). This is the diagram's API Gateway that the client calls over HTTPS.
    - Route `ANY /graphql`, using an `HTTP_PROXY` integration with `connection_type = VPC_LINK` to the NLB.
    - Stage `<env>`.
    - CORS: the `OPTIONS` preflight is proxied to the gateway pod, and FastAPI `CORSMiddleware` answers it using `CORS_ORIGINS`.
- **observability**:
  - 4 log groups `/aegisvolt/<env>/<svc>` with 7-day retention.
  - Metric filters on every group, in namespace `AegisVolt/<env>`:
    - `{ $.level = "ERROR" && $.version = "canary" }` → `CanaryErrors`
    - `{ $.event = "saga.compensated" }` → `SagaCompensations`
  - A Logs Insights saved query `aegisvolt-<env>-by-correlation`.
- **web** (S5): frontend hosting, either an S3 static website or an nginx pod (see §12).

**Outputs**: `api_url`, `nlb_arn` (the endpoint service is looked up by it), `instance_id`, `tables`, `log_groups`, `admin_token` (sensitive). `web_url` is added in S5.

**Packet path** (this is the defense answer; it goes into DEFENSE.md). It follows the diagram from top to bottom.
1. **Client to API Gateway** (`HTTPS / WSS`): the browser sends HTTPS to the REST API over the internet. No VPCE is involved, because a VPCE serves callers inside the VPC.
2. **API Gateway to cluster** ("VPC Endpoint Privado"):
   - API Gateway runs outside our VPC, so it enters through the VPC Link.
   - The VPC Link is a PrivateLink connection to the VPC endpoint service on our internal NLB.
   - The NLB is in the private subnets, so the packet never crosses the public internet.
3. **NLB to node**: the private route table's `local` route carries the packet. `node-sg` allows 30080 only from the private subnet CIDRs.
4. **Node to pod**: kube-proxy (iptables) forwards NodePort 30080 to the gateway pod. The gateway calls the 3 services by ClusterIP.
5. **Pods to DynamoDB** ("VPC Private Subnet"):
   - DynamoDB traffic uses the DynamoDB gateway VPCE, through a prefix-list route in the private route table.
   - Logs use the `logs` interface VPCE.
   - Neither path uses the internet or the NAT.
- **Why VPC Link and not a plain VPCE for step 2**:
  - An interface VPCE is a private exit from *inside* the VPC to an AWS service.
  - API Gateway → cluster is the opposite direction: from outside the VPC to inside. AWS's mechanism for that direction is the VPC Link.
  - That VPC Link is itself a VPC endpoint (PrivateLink) connection.

## 10. Kubernetes (`k8s/`, namespace `aegisvolt`)

- For each service `<svc>` there are 3 objects:
  - `Deployment <svc>-stable`: 4 replicas, labels `app=<svc>, track=stable`, env `VERSION=stable`.
  - `Deployment <svc>-canary`: 0 replicas outside a rollout, labels `app=<svc>, track=canary`, env `VERSION=canary`.
  - `Service <svc>`: selector `app=<svc>`, so traffic splits by replica ratio. During a rollout that is 1 canary out of 5 pods = **20%**.
- The gateway Service is a `NodePort` on 30080. The other Services are `ClusterIP` on port 80, targeting container port 8000.
- Probes: readiness and liveness on `GET /health`. Resources: requests 50m CPU / 64Mi memory, limit 128Mi memory.
- `scripts/build-push.sh <tag>`: builds the 4 images and pushes `ghcr.io/daemonpalace/aegisvolt-<svc>:<tag>`. The packages are public, so the node needs no pull secret.
- `scripts/deploy.sh <env> <tag>`: ships `k8s/` to the node with `aws ssm send-command` (helper `scripts/ssm.sh`), renders it there with `envsubst`, and runs `kubectl apply`. Stable keeps the tag it already runs; only the first deploy puts `<tag>` on stable directly. Canary Deployments are re-applied at 0 replicas.
- Pods get `AWS_DEFAULT_REGION` as well as `AWS_REGION`, because botocore (watchtower's client) reads only the former.
- `scripts/canary.sh <env> <svc> <tag>` runs on the node through SSM:
  1. Set the canary image to `<tag>` and scale the canary to 1.
  2. Wait up to 120 s for the canary pod to be Ready.
  3. Run a smoke loop: 50 × `GET /health` through the Service ClusterIP. Each response is counted by its `X-Version` header (traffic-split pattern from `PA-Taller-Clase4/traffic-test.js`), so the error rate is measured **per version**.
  4. Wait 60 s, then read the `CanaryErrors` sum over the last 3 minutes.
  5. **Promote** only if all of these hold:
     - the canary pod is Ready
     - the canary pod has 0 restarts
     - the canary's smoke-loop error rate is below 5%, and at least one request reached the canary
     - `CanaryErrors` = 0
  6. To promote: set the stable image to `<tag>`, run `rollout status`, and scale the canary to 0.
  7. **Otherwise**: scale the canary to 0, leave stable untouched, and exit 1. The pipeline job turns red.

## 11. CI/CD (GitHub Actions)

- **Auth**: GitHub Actions gets AWS access through OIDC.
  - **Shared account**: account 272973566676 also hosts unrelated stacks: SpottyDaemon, FinanceStack, and SAM/CDK assets. Rules that follow from that:
    - The GitHub OIDC provider **already exists**. Bootstrap reads it with a `data` source and never creates or destroys it.
    - Every resource is named `aegisvolt-*` and carries `default_tags {Project = "aegisvolt", Env = <ws>}`.
    - No role gets AdministratorAccess.
    - Humans use the IAM user `AegisVOlt-manager` (CLI profile `aegisvolt`) with the customer-managed policy `exam-aegisvolt-operator` (`bootstrap/operator-policy.json`). Root is never used.
    - Every `aegisvolt-*` role, Terraform-created or not, **must** set `permissions_boundary = arn:aws:iam::272973566676:policy/exam-aegisvolt-boundary` (`bootstrap/boundary-policy.json`). The operator policy denies creating a role or attaching a policy without it. The boundary's name sits outside `aegisvolt-*`, so neither humans nor CI can edit it. Together these block privilege escalation through a role we create.
  - **`aegisvolt-gha-plan`**: trust `repo:DaemonPalace@176678192/aegisvolt@1467661995:pull_request` (the repo uses GitHub's immutable OIDC subject, with numeric owner and repo IDs). Permissions: `ReadOnlyAccess`, plus read/write on the state bucket (plan needs the lockfile).
  - **`aegisvolt-gha-deploy`**: trust only `repo:DaemonPalace@176678192/aegisvolt@1467661995:ref:refs/heads/{dev,test,prod}`. Permissions are an inline policy:
    - ec2, elasticloadbalancing, apigateway, logs, cloudwatch, ssm: full access to those service APIs. The account has no other EC2/VPC/API Gateway REST workloads besides the default VPC.
    - dynamodb: `arn:…:table/aegisvolt-*` only.
    - s3: `aegisvolt-*` buckets only.
    - iam: role, instance-profile and policy actions on `aegisvolt-*` only, with `iam:PassRole` limited to `aegisvolt-*`.
  - Forked PRs never receive an OIDC token, so a public repo doesn't expose these roles.
- **`pr.yml`** runs on `pull_request` targeting `dev`, `test` or `prod`. The job names are the required checks:
  1. `promotion-guard`: allowed head branches are `feature/*` or `fix/*` for base `dev`, only `dev` for base `test`, and only `test` for base `prod`. Anything else fails.
  2. `lint-build`: `ruff check`, `docker build` for all 4 images, the schema export diff, `next build` (static export to `frontend/out/`), and `terraform fmt -check` + `validate`.
  3. `unit-tests`: `pytest services`, using moto for DynamoDB. The SAGA orchestrator functions are called directly; Prefect is not needed.
  4. `integration-tests`: `docker compose up` (DynamoDB Local + 4 services), then `pytest tests/integration` (tables + GraphQL).
  5. `e2e-tests`: `pytest tests/e2e` drives the full flows through GraphQL as parametrized scenarios (ported from wandersync `scripts/simulate_saga.py` and Taller-PatronSaga `scripts/simulate.py`): happy path, insufficient funds, hardware fault, idempotent replay, and the flag toggle. Each scenario asserts the final status, the **compensation order**, and the DynamoDB state.
  6. `terraform-plan`: `terraform init`, then `terraform workspace select ${{ github.base_ref }}`, then `terraform plan -no-color`. The plan is posted as a PR comment.
- **`deploy.yml`** runs on `push` to `dev`, `test` or `prod` with `concurrency: deploy-${{ github.ref_name }}`:
  1. Set `ENV=${{ github.ref_name }}`, which is the **dynamic target detection**.
  2. `terraform workspace select $ENV` and `terraform apply -auto-approve`.
  3. Build images and push them to `ghcr.io/<owner>/aegisvolt-<svc>:<sha>`.
  4. For each service whose image changed, run `canary.sh` through SSM, one matrix job per service. A change in `services/common`, the Dockerfile or `requirements.txt` counts for all 4. On the first deploy `deploy.sh` puts the tag on stable and `canary.sh` skips.
  5. Build the frontend (`next build`, static export), write `out/config.json = {apiUrl, env}`, and publish it to the env's web host (decided in S5).
  6. Run a post-deploy smoke test: the GraphQL `health` query against `api_url`.
- **Branch protection** (`scripts/protect-branches.sh`, using `gh api`) on `dev`, `test` and `prod`:
  - pull request required, with `required_approving_review_count = 0`. You are the only developer and GitHub doesn't let you approve your own PR; the PR itself is the gate.
  - `allow_force_pushes=false`
  - `allow_deletions=false`
  - `enforce_admins=true`
  - **Applied in two phases**, because GitHub waits forever on a required check that no workflow produces:
    - **S0**: `protect-branches.sh` runs without status checks. This already blocks direct pushes and force pushes, and requires a PR. PRs from S1–S3 can merge.
    - **S4**: only after `pr.yml` has run green at least once on a PR, `protect-branches.sh --with-checks` adds the 6 PR jobs as required checks in strict mode.
- The repo must be **public**, because a free plan can't protect branches on private repos.

## 12. Frontend (Next.js 16 static export)

- **Base**: the wandersync frontend (Next 16.3.8, React 19.2.8, Apollo Client 4.3.1, Tailwind 4, shadcn `ui/*`).
  - `next.config.ts` uses `output: "export"` instead of `"standalone"`, so `next build` emits plain HTML/JS/CSS in `out/` with no server.
  - The auth pages (`ingresar`, `admin`) are removed.
- **Runtime config**: at load the app fetches `/config.json` to get `apiUrl` and `env`, so one build serves every env. Apollo Client uses `pollInterval: 2000`, and the saga page stops polling once the saga reaches a terminal state.
- **Header**: an env badge and the gateway version from `health`.
- **4 panels**, each built from a reused component:
  1. **Stations**: a connector grid using `state-badge.tsx` (Taller-PatronSaga, relabelled) with the `--state-*` colour tokens. ULTRA connectors are disabled when the boost flag is off.
  2. **Start charge**: a form calling `startCharge`. It links to `/saga?id=<sagaId>`, the timeline page ported from wandersync `reservas/[id]/page.tsx`, which shows forward steps and compensations in reverse order. It becomes a query-param page because static export can't serve dynamic routes.
  3. **Feature flags**: `flags-panel.tsx`, ported from Taller-PatronSaga `dev-drawer.tsx` (Switch toggles). `setFlag` uses an admin token kept in `localStorage`.
  4. **Saga history**: `sagas(limit:20)` with status badges, plus wallet balance and transactions.
- **Hosting** (decided in S5): an S3 static website per env, `aegisvolt-web-<env>-<account>` (module `web`, output `web_url`). The account accepts the public-read bucket policy. The gateway's `CORS_ORIGINS` is set to that env's `web_url` by `deploy.yml`.
- **Styling** (changed in S5): plain CSS with design tokens (taste-skill app sheet "framed sheet", IBM Plex Sans + Plex Mono for IDs) instead of Tailwind/shadcn. Only Apollo, the formatters, the state badge, the flag switches and the saga page logic are ported from the donors.
- **Saga track**: the saga page draws the forward steps left to right and the compensations right to left underneath, so the reverse order is visible.
- `ponytail:` it polls instead of using WSS. The diagram's `HTTPS / WSS` means HTTPS is required and WSS is optional. API Gateway REST APIs can't carry WebSockets; WSS would need a separate API Gateway WebSocket API. Add one only if "real time" is challenged.

## 13. Prefect (`prefect/flows.py`, Prefect 3.8.6, Prefect Cloud free workspace)

- Prefect **observes**; it does not run the SAGA (§6).
- Donor patterns:
  - `create_table_artifact` and `.serve(cron=…)` from wandersync `ingestion/flows/ingest.py`
  - task `retries`/`retry_delay_seconds` and a quality gate that raises, from `Taller-DASK/flows/clean_pipeline.py`
  - the `saga_audit` cross-checks, rewritten from wandersync `scripts/reconcile.sql`
  - reading runs and artifacts back in `scripts/prefect_report.py`, from wandersync `scripts/ingestion_report.py`

- `env_health` flow, scheduled every 5 min with `serve(cron=…)`: one task per env.
  - It calls GraphQL `health` on that env's `api_url`.
  - It runs `describe_table` on the env's 5 tables.
  - It publishes a table artifact with env × status × latency.
- `saga_audit` flow, scheduled every 5 min: one task per env.
  - It scans `sagas` and classifies each saga as `COMPLETED`, `COMPENSATED`, `FAILED`, `STOPPED`, or **ORPHAN**. ORPHAN means `STARTED` for more than 5 min, or `COMPENSATION_FAILED`.
  - **Cross-checks**:
    - For COMPENSATED: a CHARGE must have its REFUND, and the connector must not be held by this saga.
    - For COMPLETED: the connector must be `RESERVED` by this saga, or the saga must be STOPPED.
  - It publishes a markdown artifact with counts and a list of inconsistencies, and the run **fails** if any orphans are found.
- `prefect/envs.json` maps each env to `{api_url, region}`. `scripts/demo.sh` fills it from `terraform output`.

## 14. Local development

- The compose file is based on wandersync's `x-service` anchor (shared network, env, restart policy, health check, ports bound to 127.0.0.1). `docker compose up` starts `dynamodb-local`, a `seed` one-shot container (`scripts/seed_local.py` creates the `aegisvolt-*-local` tables and loads `seed/seed.json` with dev flag defaults), and the 4 services. Admin token locally: `local-admin`. The frontend dev server joins in S5.
- The gateway listens on `localhost:8080/graphql` and the UI (`next dev`) on `localhost:3000`.

## 15. Stage acceptance criteria (review gates)

| Stage | Done when |
|---|---|
| **S0 Bootstrap** | `aws s3api get-bucket-versioning` returns `Enabled`. Both GitHub roles exist and the pre-existing OIDC provider is unchanged. The repo has `dev`, `test` and `prod` branches. `git push origin test` and `git push --force origin prod` are rejected by GitHub. A test PR into `dev` can still be merged (no required checks yet). |
| **S1 Infra** | `terraform workspace list` shows dev, test and prod. `terraform apply` in dev succeeds. `aws s3 ls s3://<state>/env:/dev/aegisvolt/` shows the state, and 2+ versions exist after a second apply. `kubectl get nodes` (through SSM) is Ready. Selecting the `default` workspace makes plan fail. |
| **S2 Services** | `docker compose up` is healthy. Unit tests pass. A local `startCharge` gives each expected outcome: happy path → COMPLETED; LOWFUNDS → COMPENSATED with the connector AVAILABLE; fault flag on → COMPENSATED with a REFUND txn and the connector AVAILABLE; replaying a compensation changes nothing. |
| **S3 K8s/Canary** | The dev `api_url/graphql` answers `health`. `aws ec2 describe-vpc-endpoint-service-configurations` shows the endpoint service on the NLB, with API Gateway as an accepted connection. A canary with a deliberately broken tag is rolled back and stable keeps serving. A canary with a good tag is promoted. |
| **S4 CI/CD** | `protect-branches.sh --with-checks` has run, and the branch settings list the 6 required checks. The `feature/x → dev` PR shows all 6 checks green and plans against the `dev` workspace. The merge triggers an apply in dev only. A `feature/x → test` PR fails `promotion-guard`. |
| **S5 Frontend** | `next build` emits `out/` with no server-only features. The dev web URL shows stations, flags and sagas. Toggling the fault flag in dev and starting a charge shows a compensated timeline. The prod UI is unchanged. |
| **S6 Observability** | A failed saga's `correlation_id` is found with CloudWatch Logs Insights in `/aegisvolt/dev/*`. Both Prefect flows show runs and artifacts in the UI. |
| **S7 Integration** | `dev → test → prod` promotion PRs are merged and all 3 envs are deployed. `scripts/demo.sh` runs every section-6 question without errors. `DEFENSE.md` is complete. |

## 16. Out of scope (deliberate)

- Real EKS: k3s instead.
- Multi-node HA.
- WSS subscriptions: polling instead.
- Service mesh, Argo Rollouts, Unleash or AppConfig.
- Custom domain/TLS on the web bucket (HTTP S3 website).
- Floci.
- No private API Gateway or `execute-api` VPCE. The diagram has one API Gateway, and its private hop is the VPC Link.

## 17. Reuse map (donor → AegisVolt)

Abbreviations: **W** = `../wandersync`, **T** = `../Taller-PatronSaga`, **G** = `../Taller-GraphQL`, **D** = `../Taller-DASK`, **C4** = `../PA-Taller-Clase4`. Nothing could be reused for Terraform, GitHub Actions, k8s manifests, boto3, watchtower or pytest, so those are new code.

| Donor | Target | What changes |
|---|---|---|
| W `services/common/web.py` (`make_app`) | `services/common/web.py` | psycopg health check → DynamoDB `describe_table`. Add JSON logging, `X-Correlation-ID` contextvar middleware, `X-Version` header |
| W `services/orders-billing/saga.py` | `services/gateway/saga.py` | Prefect decorators removed. Keep the step trail (now the `sagas` table), the 1-retry forward loop, timeout = compensate, and compensation backoff 1/2/4/8/16 s. Steps become reserve → charge → activate |
| W `services/orders-billing/app.py` | `services/gateway/app.py` (startup, `startCharge`) | Idempotency key → DynamoDB conditional put. Startup recovery scans `STARTED` sagas |
| W `services/common/participant.py` | `services/reservation/app.py`, `services/dispatch/app.py` | SQL → DynamoDB conditional writes; keep the tombstone for a release that arrives before its reserve. Failure injection reads `SIMULATE_CHARGER_HARDWARE_FAULT` |
| T `services/account-service/app/core.py` | `services/billing/app.py` | Postgres ledger + idempotency records → one `TransactWriteItems` (§5) |
| W `services/gateway/app.py` | `services/gateway/app.py` | Keep the Strawberry `GraphQLRouter`, `MaxAliasesLimiter`, CORS and security headers. Sessions removed; `X-Admin-Token` added for admin mutations |
| W `services/gateway/schema.py`, G `backend/schema.graphql` | `services/gateway/schema.py` | Keep the enum, type and `userErrors` payload shapes; the domain comes from §7 |
| T `shared/delay.py` | — | Not needed: the orchestrator sleeps `SAGA_STEP_DELAY_SECONDS` between steps |
| W `services/*/Dockerfile` | `services/Dockerfile` | One recipe with `ARG SERVICE` (python:3.12-slim, non-root uid 10001). A single pinned `services/requirements.txt`: fastapi 0.143.0, strawberry-graphql 0.332.1, uvicorn 0.54.0, pydantic 2.14.0, httpx 0.28.1, python-json-logger 4.2.0, boto3, watchtower |
| W `docker-compose.yml` | `docker-compose.yml` | Supabase, Dask and ingestion removed; `amazon/dynamodb-local` + seed one-shot added |
| W `frontend/` (apollo, gql, `ui/*`, `status-badge.tsx`) | `frontend/` | `output: "export"`, auth pages removed, runtime `/config.json` |
| W `frontend/src/app/reservas/[id]/page.tsx` | `frontend/src/app/saga/page.tsx` | Dynamic route → `?id=` query page; steps relabelled |
| T `frontend/src/components/dev-drawer.tsx` | `frontend/src/components/flags-panel.tsx` | Switches wired to `flags` / `setFlag` |
| T `frontend/src/components/state-badge.tsx` + `globals.css` tokens | `frontend/src/components/state-badge.tsx` | Relabelled for connector and saga states |
| W `scripts/simulate_saga.py`, T `scripts/simulate.py` | `tests/e2e/test_saga.py` | Scenario tables → `pytest.mark.parametrize`; assert the compensation order |
| W `scripts/test_participants.py`, G `backend/test/commands.test.js` | `services/test_unit.py` + `tests/integration/test_stack.py` | Idempotency + tombstone on moto; 3 parallel `startCharge` on one connector → exactly 1 wins, against the stack |
| W `ingestion/flows/ingest.py`, D `flows/clean_pipeline.py` | `prefect/flows.py` | Artifacts, `.serve(cron)`, task retries, quality gate; Dask removed |
| W `scripts/reconcile.sql` | `saga_audit` cross-checks | SQL → DynamoDB scan assertions |
| W `scripts/ingestion_report.py` | `scripts/prefect_report.py` | Reads flow runs and artifacts back for the S6 gate |
| C4 `traffic-test.js` | `scripts/canary.sh` smoke loop | Counts by `X-Version` and computes the error rate per version; exit 1 if the canary error rate is ≥ 5% |
| W `docs/design.md` ADR-05/06 | `DEFENSE.md` | Wording for the idempotency and compensation answers |

**Not reused**:
- RabbitMQ choreography (T `shared/rabbitmq.py`, `consumer.py`): the design uses orchestration.
- pg_graphql (W `gateway/catalog.py`): Postgres-only.
- Argon2 sessions (W `gateway/security.py`): there are no user accounts.
- Dask.
- primer-parcial: TypeScript Lambdas + Supabase.
