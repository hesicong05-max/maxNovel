export const PENDING_PROJECT_OPERATION_PREFIX = "novel_pending_planning_operation_v1";

export function pendingProjectOperationKey(userId: string, projectId: string): string {
  return `${PENDING_PROJECT_OPERATION_PREFIX}:${encodeURIComponent(userId)}:${encodeURIComponent(projectId)}`;
}

export function clearPendingProjectOperationRecord(userId: string, projectId: string): boolean {
  try {
    sessionStorage.removeItem(pendingProjectOperationKey(userId, projectId));
    return true;
  } catch {
    return false;
  }
}
