# Kubernetes

`base/` contains one manifest per independently scalable process. PostgreSQL, Redis, RabbitMQ, Kafka, and S3 are external durable services in production; this base does not create unsafe single-node stateful dependencies.

Create `northstar-secrets` from a secure secret manager, replace image placeholders through an overlay, update public origins/endpoints, then run:

```shell
kubectl kustomize deploy/kubernetes/base
kubectl apply -k deploy/kubernetes/base
```

Run the migration Job as a release gate before rolling out the deployments. Never commit a populated Secret.
