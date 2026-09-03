# Stage-1 deployment networking

The single-node baseline exposes only the Control Plane listener. SQLite stores, local
File/Workspace providers, the ReferenceOrchestrator and ReferenceExecutor do not expose
network listeners. The default Control Plane bind address is loopback.

Future remote Worker/model/tool/connector flows must be documented explicitly when their
profiles are added; they must not widen the Stage-1 exposure implicitly.
