const STORAGE_PREFIX = "novel_pending_planning_operation_v1";

export interface PendingPlanningOperation<T extends object = Record<string, unknown>> {
  schema_version: 1;
  user_id: string;
  project_id: string;
  operation_key: string;
  action: string;
  target_id: string | null;
  payload: T;
  created_at: string;
}

function storageKey(userId: string, projectId: string): string {
  return `${STORAGE_PREFIX}:${encodeURIComponent(userId)}:${encodeURIComponent(projectId)}`;
}

export function createPlanningOperationKey(action: string): string {
  if (!globalThis.crypto?.randomUUID) {
    throw new Error("当前浏览器无法生成安全操作编号，已停止写入。");
  }
  return `planning:${action}:${globalThis.crypto.randomUUID()}`;
}

export function savePendingPlanningOperation<T extends object>(
  operation: PendingPlanningOperation<T>
): boolean {
  try {
    sessionStorage.setItem(
      storageKey(operation.user_id, operation.project_id),
      JSON.stringify(operation)
    );
    return true;
  } catch {
    return false;
  }
}

export function loadPendingPlanningOperation(
  userId: string,
  projectId: string
): PendingPlanningOperation | null {
  try {
    const raw = sessionStorage.getItem(storageKey(userId, projectId));
    if (!raw) return null;
    const value = JSON.parse(raw) as Partial<PendingPlanningOperation>;
    if (
      value.schema_version !== 1 ||
      value.user_id !== userId ||
      value.project_id !== projectId ||
      typeof value.operation_key !== "string" ||
      typeof value.action !== "string" ||
      !value.payload ||
      typeof value.payload !== "object" ||
      typeof value.created_at !== "string"
    ) {
      return null;
    }
    return value as PendingPlanningOperation;
  } catch {
    return null;
  }
}

export function clearPendingPlanningOperation(userId: string, projectId: string): boolean {
  try {
    sessionStorage.removeItem(storageKey(userId, projectId));
    return true;
  } catch {
    return false;
  }
}

export function shouldKeepPlanningOperation(error: unknown): boolean {
  if (!(error instanceof Error)) return true;
  const typed = error as Error & { status?: number; outcomeUnknown?: boolean };
  if (typed.outcomeUnknown) return true;
  return typed.status === undefined || (typed.status >= 500 && typed.status !== 503);
}
