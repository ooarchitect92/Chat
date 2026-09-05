# Deployment

- [`docker/`](docker/) documents the local Docker Compose topology.
- [`kubernetes/`](kubernetes/) contains production-oriented Kustomize resources, separated by workload.

Never commit populated credentials here. Use the local `.env` file, workload identity, or an external secrets manager.
