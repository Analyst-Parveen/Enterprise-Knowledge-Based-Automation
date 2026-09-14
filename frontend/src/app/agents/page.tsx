"use client";

import * as React from "react";

import { PageHeader } from "@/components/shell";
import {
  Badge,
  Button,
  Card,
  CardBody,
  CardHeader,
  CardTitle,
  Confidence,
  ErrorState,
  Label,
  Select,
  Skeleton,
  Textarea,
} from "@/components/ui";
import { useAsync } from "@/hooks/useAsync";
import { api } from "@/lib/api";
import { DEPARTMENTS, type AgentResponse, type Department, type WorkflowId } from "@/types/api";

export default function AgentsPage() {
  const workflows = useAsync(() => api.agents.workflows(), []);
  const [workflow, setWorkflow] = React.useState<WorkflowId>("policy_comparison");
  const [question, setQuestion] = React.useState(
    "Compare the 2024 and 2026 travel policies and list what changed.",
  );
  const [department, setDepartment] = React.useState<Department | "">("");
  const [result, setResult] = React.useState<AgentResponse | null>(null);
  const [busy, setBusy] = React.useState(false);
  const [error, setError] = React.useState<string | null>(null);

  async function run(event: React.FormEvent) {
    event.preventDefault();
    setBusy(true);
    setError(null);
    setResult(null);
    try {
      setResult(
        await api.agents.run({
          workflow,
          question: question.trim(),
          department: department || null,
        }),
      );
    } catch (err) {
      setError(err instanceof Error ? err.message : "The workflow failed.");
    } finally {
      setBusy(false);
    }
  }

  const selected = workflows.data?.find((w) => w.id === workflow);

  return (
    <>
      <PageHeader
        title="Agentic Workflows"
        description="Multi-step LangGraph workflows that find, analyse and compare documents, then cite their sources."
      />

      <div className="grid gap-6 lg:grid-cols-3">
        <Card className="h-fit">
          <CardHeader>
            <CardTitle>Run a workflow</CardTitle>
          </CardHeader>
          <CardBody>
            {workflows.loading ? (
              <Skeleton className="h-40" />
            ) : (
              <form onSubmit={run} className="space-y-3">
                <div>
                  <Label htmlFor="workflow">Workflow</Label>
                  <Select
                    id="workflow"
                    value={workflow}
                    onChange={(e) => setWorkflow(e.target.value as WorkflowId)}
                  >
                    {(workflows.data ?? []).map((w) => (
                      <option key={w.id} value={w.id}>
                        {w.name}
                      </option>
                    ))}
                  </Select>
                  {selected ? (
                    <p className="mt-1.5 text-xs text-muted">{selected.description}</p>
                  ) : null}
                </div>

                <div>
                  <Label htmlFor="dept">Department</Label>
                  <Select
                    id="dept"
                    value={department}
                    onChange={(e) => setDepartment(e.target.value as Department | "")}
                  >
                    <option value="">All departments</option>
                    {DEPARTMENTS.map((d) => (
                      <option key={d} value={d}>
                        {d}
                      </option>
                    ))}
                  </Select>
                </div>

                <div>
                  <Label htmlFor="question">Request</Label>
                  <Textarea
                    id="question"
                    rows={4}
                    value={question}
                    onChange={(e) => setQuestion(e.target.value)}
                  />
                </div>

                <Button type="submit" disabled={busy || !question.trim()} className="w-full">
                  {busy ? "Running…" : "Run workflow"}
                </Button>
                <p className="text-xs text-muted">
                  Workflows make several model calls, so they use the stricter 10/minute limit.
                </p>
              </form>
            )}
          </CardBody>
        </Card>

        <div className="space-y-4 lg:col-span-2">
          {error ? <ErrorState message={error} onRetry={() => setError(null)} /> : null}

          {busy ? (
            <Card>
              <CardBody>
                <p className="text-sm text-muted">
                  Planning, retrieving, analysing, synthesising, citing…
                </p>
              </CardBody>
            </Card>
          ) : null}

          {result ? (
            <>
              {result.partial ? (
                <div className="rounded-md border border-warn/40 bg-warn/10 p-3 text-xs text-warn">
                  This workflow returned a partial result
                  {result.error ? ` (${result.error})` : ""}. Treat it as incomplete.
                </div>
              ) : null}

              <Card>
                <CardHeader className="flex items-center justify-between">
                  <CardTitle>Result</CardTitle>
                  <Badge tone="accent">{result.workflow.replace(/_/g, " ")}</Badge>
                </CardHeader>
                <CardBody className="space-y-4">
                  <p className="whitespace-pre-wrap text-sm leading-relaxed text-fg">
                    {result.answer}
                  </p>

                  {result.citations.length > 0 ? (
                    <ul className="space-y-1">
                      {result.citations.map((c) => (
                        <li
                          key={`${c.source_number}-${c.chunk_id}`}
                          className="flex items-center gap-2 rounded-md bg-bg px-2 py-1.5 text-xs"
                        >
                          <Badge tone="accent">S{c.source_number}</Badge>
                          <span className="truncate text-fg">{c.document_name}</span>
                          {c.page_number ? (
                            <span className="text-muted">p.{c.page_number}</span>
                          ) : null}
                        </li>
                      ))}
                    </ul>
                  ) : null}

                  <div className="flex flex-wrap items-center gap-x-4 gap-y-2 border-t border-border pt-3 text-xs text-muted">
                    <Confidence value={result.confidence} />
                    <span className="font-mono">{result.model_used}</span>
                    <span>{result.latency_ms} ms</span>
                    <span>
                      {result.input_tokens}/{result.output_tokens} tok
                    </span>
                    <span>${result.estimated_cost.toFixed(6)}</span>
                  </div>
                </CardBody>
              </Card>

              <Card>
                <CardHeader>
                  <CardTitle>Workflow trace</CardTitle>
                </CardHeader>
                <CardBody>
                  <ol className="space-y-1.5">
                    {result.steps.map((step, index) => (
                      <li key={index} className="flex items-start gap-2 text-xs">
                        <span className="mt-0.5 w-5 shrink-0 tabular-nums text-muted">
                          {index + 1}.
                        </span>
                        <span className="font-medium text-fg">{step.node}</span>
                        <span className="text-muted">
                          {Object.entries(step)
                            .filter(([key]) => key !== "node")
                            .map(([key, value]) => `${key}=${String(value)}`)
                            .join(" · ")}
                        </span>
                      </li>
                    ))}
                  </ol>
                </CardBody>
              </Card>
            </>
          ) : null}
        </div>
      </div>
    </>
  );
}
