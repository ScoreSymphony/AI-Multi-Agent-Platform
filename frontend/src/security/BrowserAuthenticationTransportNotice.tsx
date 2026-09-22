import { Card } from "../components/States";
import type { BrowserAuthenticationTransport } from "./browserAuthenticationTransport";

export function BrowserAuthenticationTransportNotice({
  transport,
}: {
  transport: BrowserAuthenticationTransport;
}) {
  if (transport.supported) return null;

  return (
    <Card title="HTTPS required">
      <div className="stack" role="alert">
        <p>
          Browser authentication is blocked on this insecure origin. Open the platform through an
          HTTPS domain or a trusted TLS reverse proxy before creating an administrator or signing in.
        </p>
        <p>
          Direct HTTP access by server IP is intended for health diagnostics only when Secure
          session cookies are enabled. Retrying or resetting the account will not fix this transport
          requirement.
        </p>
        {transport.origin ? (
          <p className="muted">
            Current origin: <code>{transport.origin}</code>
          </p>
        ) : null}
      </div>
    </Card>
  );
}
