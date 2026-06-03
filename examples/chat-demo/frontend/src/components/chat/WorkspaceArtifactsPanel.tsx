import { useEffect, useMemo, useState } from "react";
import { Download, FileText, FolderOpen, RefreshCw } from "lucide-react";
import { useQueryClient } from "@tanstack/react-query";

import { cn } from "../../lib/cn";
import {
  useWorkspaceArtifactPreview,
  useWorkspaceArtifacts,
  workspaceArtifactsQueryKey,
} from "../../lib/workspaceArtifacts";
import type { WorkspaceArtifactView } from "../../types/api";
import { Badge } from "../ui/badge";
import { Button, buttonVariants } from "../ui/button";
import {
  Popover,
  PopoverContent,
  PopoverTrigger,
} from "../ui/popover";
import { ScrollArea } from "../ui/scroll-area";

interface WorkspaceArtifactsPanelProps {
  sessionId: string;
  disabled?: boolean;
  knownArtifacts?: WorkspaceArtifactView[];
}

export function WorkspaceArtifactsPanel({
  sessionId,
  disabled,
  knownArtifacts = [],
}: WorkspaceArtifactsPanelProps) {
  const queryClient = useQueryClient();
  const [open, setOpen] = useState(false);
  const artifactsQuery = useWorkspaceArtifacts(sessionId);
  const artifacts = mergeArtifacts(artifactsQuery.data?.artifacts ?? [], knownArtifacts);
  const [selectedPath, setSelectedPath] = useState<string | undefined>();
  const activePath = selectedPath ?? preferredArtifactPath(artifacts);
  const previewQuery = useWorkspaceArtifactPreview(
    sessionId,
    open ? activePath : undefined,
  );

  const selected = useMemo(
    () => artifacts.find((item) => item.path === activePath),
    [activePath, artifacts],
  );
  const count = artifacts.length;

  useEffect(() => {
    setSelectedPath(undefined);
  }, [sessionId]);

  return (
    <Popover open={open} onOpenChange={setOpen}>
      <PopoverTrigger asChild>
        <Button
          type="button"
          size="sm"
          variant="ghost"
          disabled={disabled}
          className="gap-1.5 text-muted-foreground"
        >
          <FolderOpen className="h-4 w-4" aria-hidden />
          Artifacts ({count})
        </Button>
      </PopoverTrigger>
      <PopoverContent
        align="end"
        sideOffset={8}
        className="w-[min(92vw,42rem)] overflow-hidden p-0"
      >
        <div className="grid min-h-[22rem] grid-cols-[13rem,minmax(0,1fr)] sm:min-h-[26rem]">
          <div className="min-w-0 border-r border-border/80 bg-muted/35">
            <div className="flex h-11 items-center justify-between gap-2 border-b border-border/70 px-3">
              <span className="text-xs font-medium uppercase tracking-wide text-muted-foreground">
                Workspace
              </span>
              <Button
                type="button"
                size="icon"
                variant="ghost"
                className="h-7 w-7"
                onClick={() => {
                  void queryClient.invalidateQueries({
                    queryKey: workspaceArtifactsQueryKey(sessionId),
                  });
                }}
                aria-label="Refresh artifacts"
              >
                <RefreshCw className="h-3.5 w-3.5" aria-hidden />
              </Button>
            </div>
            <ScrollArea className="h-[calc(26rem-2.75rem)]">
              <div className="grid gap-1 p-2">
                {artifactsQuery.isLoading ? (
                  <p className="px-2 py-6 text-center text-xs text-muted-foreground">
                    Loading artifacts...
                  </p>
                ) : artifacts.length ? (
                  artifacts.map((artifact) => (
                    <button
                      key={artifact.path}
                      type="button"
                      className={cn(
                        "grid min-w-0 gap-1 rounded-md px-2.5 py-2 text-left text-xs",
                        "hover:bg-background focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring",
                        artifact.path === activePath && "bg-background shadow-sm",
                      )}
                      onClick={() => setSelectedPath(artifact.path)}
                    >
                      <span className="flex min-w-0 items-center gap-1.5">
                        <FileText className="h-3.5 w-3.5 shrink-0 text-emerald-600" />
                        <span className="truncate font-mono">{artifact.path}</span>
                      </span>
                      <span className="flex items-center gap-1.5 text-muted-foreground">
                        <Badge variant="outline" className="h-5 rounded-md px-1.5 text-[0.65rem]">
                          {artifact.kind}
                        </Badge>
                        <span>{formatBytes(artifact.sizeBytes)}</span>
                      </span>
                    </button>
                  ))
                ) : (
                  <p className="px-2 py-6 text-center text-xs text-muted-foreground">
                    No research artifacts yet.
                  </p>
                )}
              </div>
            </ScrollArea>
          </div>
          <div className="min-w-0 bg-background">
            <div className="flex h-11 min-w-0 items-center justify-between gap-2 border-b border-border/70 px-3">
              <div className="min-w-0">
                <p className="truncate font-mono text-xs font-medium">
                  {activePath ?? "No artifact selected"}
                </p>
                {selected ? (
                  <p className="text-[0.68rem] text-muted-foreground">
                    {formatBytes(selected.sizeBytes)}
                  </p>
                ) : null}
              </div>
              {selected ? (
                <div className="flex shrink-0 items-center gap-1">
                  <a
                    className={cn(
                      buttonVariants({ variant: "ghost", size: "sm" }),
                      "h-7 gap-1 px-2 text-xs",
                    )}
                    href={artifactDownloadUrl(sessionId, selected.path)}
                    download
                  >
                    <Download className="h-3.5 w-3.5" aria-hidden />
                    {downloadLabel(selected.path)}
                  </a>
                  {selected.path.endsWith(".md") ? (
                    <a
                      className={cn(
                        buttonVariants({ variant: "ghost", size: "sm" }),
                        "h-7 gap-1 px-2 text-xs",
                      )}
                      href={artifactPdfDownloadUrl(sessionId, selected.path)}
                      download
                    >
                      <Download className="h-3.5 w-3.5" aria-hidden />
                      PDF
                    </a>
                  ) : null}
                </div>
              ) : null}
            </div>
            <ScrollArea className="h-[calc(26rem-2.75rem)]">
              <pre className="min-h-full whitespace-pre-wrap break-words p-4 font-mono text-xs leading-relaxed text-foreground">
                {previewText(
                  activePath,
                  previewQuery.isLoading,
                  previewQuery.isError,
                  previewQuery.data?.content,
                  previewQuery.data?.truncated,
                )}
              </pre>
            </ScrollArea>
          </div>
        </div>
      </PopoverContent>
    </Popover>
  );
}

function artifactDownloadUrl(sessionId: string, path: string): string {
  return `/api/workspace/${encodeURIComponent(sessionId)}/artifacts/${encodeArtifactPath(
    path,
  )}/download`;
}

function artifactPdfDownloadUrl(sessionId: string, path: string): string {
  return `/api/workspace/${encodeURIComponent(sessionId)}/artifacts/${encodeArtifactPath(
    path,
  )}/download.pdf`;
}

function downloadLabel(path: string): string {
  if (path.endsWith(".md")) {
    return "MD";
  }
  if (path.endsWith(".jsonl")) {
    return "JSONL";
  }
  if (path.endsWith(".json")) {
    return "JSON";
  }
  return "Download";
}

function encodeArtifactPath(path: string): string {
  return path.split("/").map(encodeURIComponent).join("/");
}

function preferredArtifactPath(artifacts: WorkspaceArtifactView[]): string | undefined {
  return (
    artifacts.find((item) => item.path === "research/report.md")?.path ??
    artifacts[0]?.path
  );
}

function mergeArtifacts(
  workspaceArtifacts: WorkspaceArtifactView[],
  knownArtifacts: WorkspaceArtifactView[],
): WorkspaceArtifactView[] {
  const byPath = new Map<string, WorkspaceArtifactView>();
  for (const artifact of knownArtifacts) {
    byPath.set(artifact.path, artifact);
  }
  for (const artifact of workspaceArtifacts) {
    byPath.set(artifact.path, artifact);
  }
  return Array.from(byPath.values()).sort((left, right) => {
    if (left.path === "research/report.md") {
      return -1;
    }
    if (right.path === "research/report.md") {
      return 1;
    }
    return left.path.localeCompare(right.path);
  });
}

function previewText(
  path: string | undefined,
  loading: boolean,
  error: boolean,
  content: string | undefined,
  truncated: boolean | undefined,
): string {
  if (!path) {
    return "No artifact selected.";
  }
  if (loading) {
    return "Loading preview...";
  }
  if (error) {
    return "Could not load artifact preview.";
  }
  const suffix = truncated ? "\n\n[preview truncated]" : "";
  return `${content ?? ""}${suffix}`;
}

function formatBytes(value: number): string {
  if (value >= 1024 * 1024) {
    return `${(value / (1024 * 1024)).toFixed(1)} MB`;
  }
  if (value >= 1024) {
    return `${(value / 1024).toFixed(1)} KB`;
  }
  return `${Math.max(0, Math.round(value))} B`;
}
