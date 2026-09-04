import type { ControlPlaneClient } from "../api/client";
import type { VerificationClient } from "../api/verification";
import { VerificationSummary } from "../components/VerificationSummary";
import { RunDetailPage } from "./Pages";
import { ReferenceDetailPage } from "./ReferencePages";
import type { ReferenceCollection } from "../api/references";
import { TaskDetailPage } from "./TaskDetailPage";

export function VerificationBoundTaskDetailPage({
  client,
  verificationClient,
  taskId,
}: {
  client: ControlPlaneClient;
  verificationClient: VerificationClient;
  taskId: string;
}) {
  return (
    <>
      <TaskDetailPage client={client} taskId={taskId} />
      <VerificationSummary client={verificationClient} scope={{ kind: "task", id: taskId }} />
    </>
  );
}

export function VerificationBoundRunDetailPage({
  client,
  verificationClient,
  runId,
}: {
  client: ControlPlaneClient;
  verificationClient: VerificationClient;
  runId: string;
}) {
  return (
    <>
      <RunDetailPage client={client} runId={runId} />
      <VerificationSummary client={verificationClient} scope={{ kind: "run", id: runId }} />
    </>
  );
}

export function VerificationBoundReferenceDetailPage({
  client,
  verificationClient,
  collection,
  resourceId,
}: {
  client: ControlPlaneClient;
  verificationClient: VerificationClient;
  collection: ReferenceCollection;
  resourceId: string;
}) {
  return (
    <>
      <ReferenceDetailPage client={client} collection={collection} resourceId={resourceId} />
      {collection === "results" ? (
        <VerificationSummary client={verificationClient} scope={{ kind: "result", id: resourceId }} />
      ) : null}
    </>
  );
}
