import { ControlPlaneCollectionClient } from "./collections";
import { ApiTransport } from "./transport";
import type { ApiTransportOptions } from "./transport";
import type { JsonValue, ListQuery, Page } from "./types";

export interface CanonicalFile {
  id: string;
  type: "file";
  project_id: string | null;
  owner_ref: string;
  created_by: string;
  created_at: string;
  size_bytes: number;
  sha256: string;
  state: string;
  content_type: string | null;
  artifact_ids: string[];
  metadata: Record<string, JsonValue>;
}

export interface FilesClientOptions extends ApiTransportOptions {
  transport?: ApiTransport;
}

const FILE_COLLECTION = "files";

export class FilesClient {
  readonly baseUrl: string;
  private readonly collections: ControlPlaneCollectionClient;

  constructor(options: FilesClientOptions = {}) {
    const transport = options.transport ?? new ApiTransport(options);
    this.baseUrl = transport.baseUrl;
    this.collections = new ControlPlaneCollectionClient({ transport });
  }

  listFiles(query: ListQuery = {}): Promise<Page<CanonicalFile>> {
    return this.collections.list<CanonicalFile>(FILE_COLLECTION, query);
  }

  getFile(fileId: string): Promise<CanonicalFile> {
    const id = fileId.trim();
    if (!id) throw new Error("File ID is required");
    return this.collections.get<CanonicalFile>(FILE_COLLECTION, id);
  }
}
