# Deployment

- [`docker/`](../docker/) documents the local Docker Compose topology.
- [`kubernetes/`](../kubernetes/) contains production-oriented Kustomize resources, separated by workload.

Never commit populated credentials here. Use the local `.env` file, workload identity, or an external secrets manager.

## Delivery controls

Pull requests pass independent frontend, backend, deployment-schema, secret, dependency, filesystem-security, container-vulnerability, migration, and full-topology smoke gates. CodeQL analyzes Python and TypeScript separately.

Version tags publish API and web images to GHCR using immutable SHA tags. The release workflow emits an SBOM and maximum-mode build provenance, blocks critical runtime vulnerabilities, and attaches a GitHub artifact attestation. Publishing is not deployment: a protected production environment should promote the verified digest through `kubernetes/overlays/production` after human approval, backup verification, and the migration gate.

Set the repository variable `S3_PUBLIC_ENDPOINT_URL` to the production HTTPS object-store origin before creating a release; the web image intentionally refuses to publish with a missing or non-HTTPS upload origin. Configure `KUBE_CONFIG_B64` only in the protected `production` environment, or replace that generic credential step with the target cloud's OIDC login action.

Configure branch protection on `main` to require CI, CodeQL, an approving review, resolved conversations, and a linear history. Protect version tags, prohibit force pushes, restrict GitHub Actions to approved SHA-pinned actions, and require the production environment approval separately in repository settings.
