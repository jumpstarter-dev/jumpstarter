# Best Practices

## Labeling Strategy

Develop a consistent labeling strategy for your exporters to make device
selection straightforward:

- **System Properties**: `arch=arm64`, `cpu=cortex-a53`
- **Organization**: `team=platform`, `project=widget`
- **Capabilities**: `has-video=true`, `has-can=true`
- **Environment**: `env=dev`, `env=production`

## Resource Management

Implement these practices to ensure efficient use of shared systems:

- Set appropriate {term}`lease` timeouts to prevent orphaned resources
- Use CI systems' concurrency controls to manage test parallelism
- Implement monitoring and alerting for device availability
- Create "pools" of identical devices to improve scalability

## Security Considerations

When deploying Jumpstarter in a multi-user environment:

- Use role-based access control to limit which users can access which devices
- Restrict driver access to prevent untrusted code execution
- Isolate the Jumpstarter network from production systems
- Rotate JWT tokens regularly for enhanced security
