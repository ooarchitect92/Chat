# Redis

Redis provides caching, rate limiting, and transient coordination. Local ports, health checks, persistence, and credentials are defined by the `redis` service in the root `compose.yaml`.

Production deployments should provide Redis through `REDIS_URL` and use authentication, TLS, persistence, monitoring, and high availability appropriate to the environment.
