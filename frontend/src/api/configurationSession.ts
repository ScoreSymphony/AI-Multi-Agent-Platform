import { useMemo } from "react";
import { BrowserSessionClient } from "./browserSession";
import { ControlPlaneClient } from "./client";
import { ControlPlaneCollectionClient } from "./collections";
import { ConfigurationClient } from "./configuration";

/**
 * Compose focused configuration clients over the same canonical browser-session
 * boundary used by the main shell. This keeps unsafe configuration mutations behind
 * HttpOnly-session + CSRF handling and never introduces provider-direct calls.
 */
export function useConfigurationSession(core: ControlPlaneClient) {
  return useMemo(() => {
    const session = new BrowserSessionClient({ baseUrl: core.baseUrl });
    return {
      configuration: new ConfigurationClient({ transport: session.transport }),
      collections: new ControlPlaneCollectionClient({ transport: session.transport }),
    };
  }, [core]);
}
