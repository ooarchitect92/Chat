# MinIO

MinIO provides S3-compatible object storage for local development. Local ports, health checks, persistence, and credentials are defined by the `minio` service in the root `compose.yaml`.

Production deployments can use MinIO or another S3-compatible provider through the backend object-storage environment variables, with encryption, lifecycle policies, backups, and restricted credentials enabled.
