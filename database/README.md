# Local infrastructure support

- `postgres/` contains local database initialization and restricted runtime roles.
- [`../scripts/smoke.ps1`](../scripts/smoke.ps1) and [`../scripts/smoke.sh`](../scripts/smoke.sh) verify the complete local environment.

Docker and Kubernetes definitions are separated under [`../docker/`](../docker/) and [`../kubernetes/`](../kubernetes/). Application business logic does not belong here.
