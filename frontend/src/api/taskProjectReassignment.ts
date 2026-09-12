import { ApiTransport } from "./transport";
import type { ApiTransportOptions } from "./transport";
import type { CanonicalTask } from "./types";

export interface TaskProjectReassignmentClientOptions extends ApiTransportOptions {
  transport?: ApiTransport;
}

export class TaskProjectReassignmentClient {
  readonly baseUrl: string;
  private readonly transport: ApiTransport;

  constructor(options: TaskProjectReassignmentClientOptions = {}) {
    this.transport = options.transport ?? new ApiTransport(options);
    this.baseUrl = this.transport.baseUrl;
  }

  move(taskId: string, destinationProjectId: string | null): Promise<CanonicalTask> {
    return this.command<CanonicalTask>("task.project.move", taskId, {
      destination_project_id: destinationProjectId,
    });
  }

  private async command<T>(
    command: string,
    resourceRef: string,
    payload: object,
  ): Promise<T> {
    return this.transport.request<T>(`/commands/${encodeURIComponent(command)}`, {
      method: "POST",
      body: { resource_ref: resourceRef, ...payload },
      idempotencyKey: crypto.randomUUID(),
    });
  }
}
