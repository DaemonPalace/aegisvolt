# AegisVolt

AegisVolt is a cloud-native charging-management platform for electric delivery fleets. It was built as the final exam for *Patrones Arquitectónicos Avanzados*.

- The full design is in [SPEC.md](SPEC.md): stack, GraphQL schema, SAGA flow, Terraform modules, CI/CD, and the acceptance criteria for each stage.
- Branches `dev`, `test` and `prod` map one-to-one to the Terraform workspaces and AWS environments of the same name.
- Changes are promoted only through pull requests, in this order: `feature/*` → `dev` → `test` → `prod`.

## Bootstrap (one time)

```bash
AWS_PROFILE=aegisvolt terraform -chdir=bootstrap init
AWS_PROFILE=aegisvolt terraform -chdir=bootstrap apply   # versioned state bucket + GitHub OIDC roles
scripts/protect-branches.sh                               # PR-only, no force push (add --with-checks once CI exists)
```

## Reset an environment to the seed data

Empties every table of the environment and reloads `seed/seed.json`. This erases added connectors, new fleets, deposits, sagas, sessions and telemetry, and puts the flags back to that environment's defaults. The end-to-end tests assume this state.

```sh
# local (compose)
AWS_ACCESS_KEY_ID=local AWS_SECRET_ACCESS_KEY=local DYNAMODB_ENDPOINT=http://localhost:8000 \
  .venv/bin/python scripts/seed_local.py local dev --reset
# AWS: table suffix and flag defaults are both the env name; prod also needs I_MEAN_PROD=1
AWS_PROFILE=aegisvolt .venv/bin/python scripts/seed_local.py dev dev --reset
AWS_PROFILE=aegisvolt .venv/bin/python scripts/seed_local.py test test --reset
```
