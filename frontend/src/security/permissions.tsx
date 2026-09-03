import { createContext, useContext, type ReactNode } from "react";

export type PermissionHint = "allowed" | "denied" | "unknown";
export type PermissionEvaluator = (action: string, resourceRef?: string) => PermissionHint;

const PermissionContext = createContext<PermissionEvaluator | null>(null);

export function PermissionHintsProvider({
  evaluate,
  children,
}: {
  evaluate?: PermissionEvaluator;
  children: ReactNode;
}) {
  return (
    <PermissionContext.Provider value={evaluate ?? null}>{children}</PermissionContext.Provider>
  );
}

export function usePermissionHint(action: string, resourceRef?: string): PermissionHint {
  const evaluate = useContext(PermissionContext);
  return evaluate?.(action, resourceRef) ?? "unknown";
}
