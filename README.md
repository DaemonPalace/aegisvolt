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
