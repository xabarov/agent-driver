import {
  useMutation,
  useQuery,
  useQueryClient,
  type QueryClient,
  type UseQueryResult,
} from "@tanstack/react-query";

import { createSession, deleteSession, getSession, listSessions } from "./api";
import type {
  CreateSessionRequest,
  DeleteSessionResponse,
  SessionDetailView,
  SessionsListResponse,
} from "../types/api";

export const sessionsQueryKey = ["sessions"] as const;

export function sessionDetailQueryKey(sessionId: string): readonly [string, string] {
  return [...sessionsQueryKey, sessionId] as const;
}

export async function invalidateSessions(queryClient: QueryClient): Promise<void> {
  await queryClient.invalidateQueries({ queryKey: sessionsQueryKey });
}

export function useSessions(): UseQueryResult<SessionsListResponse> {
  return useQuery({
    queryKey: sessionsQueryKey,
    queryFn: listSessions,
  });
}

export function useSession(sessionId: string): UseQueryResult<SessionDetailView> {
  return useQuery({
    queryKey: sessionDetailQueryKey(sessionId),
    queryFn: () => getSession(sessionId),
    enabled: Boolean(sessionId),
  });
}

export function useCreateSession() {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: (payload: CreateSessionRequest) => createSession(payload),
    onSuccess: async (session) => {
      queryClient.setQueryData(sessionDetailQueryKey(session.session_id), session);
      await invalidateSessions(queryClient);
    },
  });
}

export function useDeleteSession() {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: (sessionId: string): Promise<DeleteSessionResponse> => deleteSession(sessionId),
    onSuccess: async (_result, sessionId) => {
      queryClient.removeQueries({ queryKey: sessionDetailQueryKey(sessionId) });
      await invalidateSessions(queryClient);
    },
  });
}
