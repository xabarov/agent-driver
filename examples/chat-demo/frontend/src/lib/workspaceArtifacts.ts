import { useQuery, type UseQueryResult } from "@tanstack/react-query";

import {
  fetchWorkspaceArtifactPreview,
  fetchWorkspaceArtifacts,
} from "./api";
import type {
  WorkspaceArtifactPreviewResponse,
  WorkspaceArtifactsResponse,
} from "../types/api";

export function workspaceArtifactsQueryKey(
  sessionId: string,
): readonly [string, string, string] {
  return ["workspace", sessionId, "artifacts"] as const;
}

export function workspaceArtifactPreviewQueryKey(
  sessionId: string,
  path: string,
): readonly [string, string, string, string] {
  return ["workspace", sessionId, "artifact-preview", path] as const;
}

export function useWorkspaceArtifacts(
  sessionId: string,
): UseQueryResult<WorkspaceArtifactsResponse> {
  return useQuery({
    queryKey: workspaceArtifactsQueryKey(sessionId),
    queryFn: () => fetchWorkspaceArtifacts(sessionId),
    enabled: Boolean(sessionId),
  });
}

export function useWorkspaceArtifactPreview(
  sessionId: string,
  path: string | undefined,
): UseQueryResult<WorkspaceArtifactPreviewResponse> {
  return useQuery({
    queryKey: workspaceArtifactPreviewQueryKey(sessionId, path ?? ""),
    queryFn: () => fetchWorkspaceArtifactPreview(sessionId, path ?? ""),
    enabled: Boolean(sessionId && path),
  });
}
