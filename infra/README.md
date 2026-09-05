# Local infrastructure support

- `postgres/` contains local database initialization and restricted runtime roles.
- `smoke.ps1` and `smoke.sh` verify the complete local environment.

Deployment definitions are separated under [`../deploy/`](../deploy/). Application business logic does not belong here.
