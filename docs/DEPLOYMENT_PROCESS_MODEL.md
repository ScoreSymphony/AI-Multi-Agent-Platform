# Stage-1 process model

The initial profile deliberately uses one Control Plane process plus in-process reference
orchestration/execution and local persistence providers. This is a deployment composition,
not a canonical architecture constraint. Later single-server profiles may split optional
services/processes while preserving the same platform contracts.
