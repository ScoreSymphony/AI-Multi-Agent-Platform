import { AppLink } from "../app/router";
import { EmptyState } from "../components/States";

export function NotFoundPage({ path }: { path: string }) {
  return (
    <div className="stack">
      <header className="page-header">
        <p className="eyebrow">Navigation</p>
        <h1>Page not found</h1>
        <p>No maintained Web route matches <code>{path}</code>.</p>
      </header>
      <EmptyState
        title="Unknown route"
        detail="The address does not map to a claimed V1 Web surface. Optional subsystems use a separate unavailable state."
      />
      <div className="actions">
        <AppLink href="/">Return to platform overview</AppLink>
      </div>
    </div>
  );
}
