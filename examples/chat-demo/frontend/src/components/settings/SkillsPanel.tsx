import { useState } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { Eye, ShieldAlert, ShieldCheck, Upload } from "lucide-react";

import { fetchSkill, fetchSkills, uploadSkill } from "../../lib/api";
import { cn } from "../../lib/cn";
import type { SkillManifestView } from "../../types/api";
import { Badge } from "../ui/badge";
import { Button } from "../ui/button";
import { Textarea } from "../ui/textarea";

function skillSummary(skill: SkillManifestView): string {
  return skill.whenToUse || skill.description || skill.name;
}

export function SkillsPanel() {
  const queryClient = useQueryClient();
  const [selected, setSelected] = useState<string | null>(null);
  const [uploadOpen, setUploadOpen] = useState(false);
  const [uploadName, setUploadName] = useState("");
  const [uploadContent, setUploadContent] = useState("");
  const skillsQuery = useQuery({ queryKey: ["skills"], queryFn: fetchSkills });
  const skillQuery = useQuery({
    queryKey: ["skill", selected],
    queryFn: () => fetchSkill(selected ?? ""),
    enabled: Boolean(selected),
  });
  const uploadMutation = useMutation({
    mutationFn: uploadSkill,
    onSuccess: (response) => {
      setSelected(response.skill.name);
      setUploadName("");
      setUploadContent("");
      setUploadOpen(false);
      void queryClient.invalidateQueries({ queryKey: ["skills"] });
    },
  });

  const skills = skillsQuery.data?.skills ?? [];
  return (
    <div className="grid gap-3">
      <div className="flex items-center justify-between gap-2">
        <div className="text-xs font-medium">Skills</div>
        <Button
          type="button"
          size="sm"
          variant="ghost"
          className="h-7 gap-1.5"
          onClick={() => setUploadOpen((value) => !value)}
        >
          <Upload className="h-3.5 w-3.5" />
          Upload
        </Button>
      </div>
      {uploadOpen ? (
        <div className="grid gap-2 rounded-md border border-border/80 bg-background/70 p-2">
          <input
            value={uploadName}
            onChange={(event) => setUploadName(event.target.value)}
            placeholder="skill-name"
            className="h-8 rounded-md border border-input bg-background px-2 text-xs outline-none focus-visible:ring-2 focus-visible:ring-ring"
          />
          <Textarea
            value={uploadContent}
            onChange={(event) => setUploadContent(event.target.value)}
            placeholder="---&#10;name: my-skill&#10;---&#10;# Workflow"
            rows={4}
            className="text-xs"
          />
          <Button
            type="button"
            size="sm"
            disabled={!uploadName.trim() || !uploadContent.trim()}
            onClick={() =>
              uploadMutation.mutate({
                name: uploadName,
                content: uploadContent,
              })
            }
          >
            Install
          </Button>
        </div>
      ) : null}
      <div className="max-h-64 space-y-2 overflow-y-auto pr-1">
        {skills.map((skill) => (
          <button
            key={skill.digest}
            type="button"
            className={cn(
              "w-full rounded-md border p-2 text-left transition-colors",
              selected === skill.name
                ? "border-primary/50 bg-primary/5"
                : "border-border/75 bg-background/60 hover:bg-muted/60",
            )}
            onClick={() => setSelected(skill.name)}
          >
            <div className="flex items-center gap-2">
              {skill.trusted ? (
                <ShieldCheck className="h-3.5 w-3.5 text-emerald-600" />
              ) : (
                <ShieldAlert className="h-3.5 w-3.5 text-amber-600" />
              )}
              <span className="min-w-0 flex-1 truncate text-xs font-medium">
                {skill.name}
              </span>
              <Badge variant="outline" className="text-[0.65rem]">
                {skill.source}
              </Badge>
            </div>
            <p className="mt-1 line-clamp-2 text-xs text-muted-foreground">
              {skillSummary(skill)}
            </p>
          </button>
        ))}
      </div>
      {skillQuery.data ? (
        <div className="rounded-md border border-border/80 bg-background/70 p-2">
          <div className="mb-1 flex items-center gap-2 text-xs font-medium">
            <Eye className="h-3.5 w-3.5" />
            {skillQuery.data.skill.name}
          </div>
          {skillQuery.data.skill.safetyWarnings.length ? (
            <div className="mb-2 rounded border border-amber-500/30 bg-amber-500/10 px-2 py-1 text-xs text-amber-700">
              {skillQuery.data.skill.safetyWarnings[0]}
            </div>
          ) : null}
          <pre className="max-h-40 overflow-y-auto whitespace-pre-wrap break-words text-xs text-muted-foreground">
            {skillQuery.data.content}
          </pre>
        </div>
      ) : null}
    </div>
  );
}
