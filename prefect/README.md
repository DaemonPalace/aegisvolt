# Prefect observability (SPEC §13)

Two flows, both scheduled every 5 minutes. Prefect only observes: it never runs the SAGA.

| Flow | Per env | Artifacts | Run fails when |
|---|---|---|---|
| `env_health` | GraphQL `health` with latency, `describe_table` on the 5 tables | table `env-health` | an env is down/degraded or a table is missing |
| `saga_audit` | reads the sagas, billing and reservations tables; rules in `audit.py` | table `saga-audit-summary`, markdown `saga-audit-<env>` | any orphan or inconsistency |

The audit puts each saga in one class: RUNNING, CHARGING, FINISHED, STOPPED, COMPENSATED, FAILED or **ORPHAN**.
- An **ORPHAN** is one of: STARTED for over 5 min; COMPENSATION_FAILED; charging more than 5 min past `endsAt`; or charging with no `endsAt` for over 1 h.
- The **inconsistency** checks are:
  - a COMPENSATED charge with no REFUND;
  - a finished saga that still holds its connector;
  - a charging saga that does not hold its connector;
  - a charge with no CHARGE in the wallet;
  - a connector held by a saga that does not exist.
- A connector left OUT_OF_SERVICE after a hardware fault is expected. It is listed, not flagged.

Envs live in `envs.json`. An env with `api_url: null` (test and prod until S7) is reported as "not deployed".

```sh
uv venv --python 3.12 prefect/.venv && uv pip install --python prefect/.venv/bin/python -r prefect/requirements.txt
docker compose --profile observability up -d prefect-server          # UI on http://localhost:4200
export PREFECT_API_URL=http://localhost:4200/api AWS_PROFILE=aegisvolt
prefect/.venv/bin/python prefect/flows.py run local dev     # one run of each flow now
prefect/.venv/bin/python prefect/flows.py serve             # both flows on */5 until stopped
prefect/.venv/bin/python scripts/prefect_report.py          # latest runs + artifacts, as text
```

To use Prefect Cloud instead, set `PREFECT_API_URL` and `PREFECT_API_KEY` to the workspace's values. Nothing else changes.

## CloudWatch trace (the other half of S6)

```sh
AWS_PROFILE=aegisvolt scripts/trace.sh dev --failed          # latest failed/compensated sagas + correlation_id
AWS_PROFILE=aegisvolt scripts/trace.sh dev <saga-id>         # that saga's lines from all 4 services, in order
```

The same two queries are saved in the console under Logs Insights: `aegisvolt-<env>-failed-sagas` and `aegisvolt-<env>-by-correlation`.
