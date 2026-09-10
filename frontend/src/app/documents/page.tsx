"use client";

import * as React from "react";

import { PageHeader } from "@/components/shell";
import {
  Badge,
  Button,
  Card,
  CardBody,
  EmptyState,
  ErrorState,
  Input,
  Label,
  Select,
  Skeleton,
  statusTone,
  Table,
  Td,
  Th,
} from "@/components/ui";
import { useAsync } from "@/hooks/useAsync";
import { api } from "@/lib/api";
import { DEPARTMENTS, type Department } from "@/types/api";

function formatBytes(bytes: number): string {
  if (bytes < 1024) return `${bytes} B`;
  if (bytes < 1024 * 1024) return `${(bytes / 1024).toFixed(0)} KB`;
  return `${(bytes / 1024 / 1024).toFixed(1)} MB`;
}

export default function DocumentsPage() {
  const [filter, setFilter] = React.useState<Department | "">("");
  const docs = useAsync(
    () => api.documents.list({ limit: 100, department: filter || undefined }),
    [filter],
  );

  const [file, setFile] = React.useState<File | null>(null);
  const [uploadDept, setUploadDept] = React.useState<Department | "">("");
  const [uploading, setUploading] = React.useState(false);
  const [notice, setNotice] = React.useState<string | null>(null);
  const [error, setError] = React.useState<string | null>(null);

  async function upload(event: React.FormEvent) {
    event.preventDefault();
    if (!file) return;
    setUploading(true);
    setError(null);
    setNotice(null);
    try {
      const result = await api.documents.upload(file, uploadDept || undefined);
      setNotice(`${result.document.name} accepted — ingestion queued.`);
      setFile(null);
      docs.reload();
    } catch (err) {
      setError(err instanceof Error ? err.message : "Upload failed.");
    } finally {
      setUploading(false);
    }
  }

  async function remove(id: string, name: string) {
    if (!window.confirm(`Delete “${name}”? Its chunks are removed from search too.`)) return;
    try {
      await api.documents.remove(id);
      docs.reload();
    } catch (err) {
      setError(err instanceof Error ? err.message : "Delete failed.");
    }
  }

  return (
    <>
      <PageHeader
        title="Documents"
        description="PDF, Word, spreadsheets, images, diagrams, audio and video. Everything is scoped to your tenant."
      />

      {/* ---- upload ------------------------------------------------------ */}
      <Card className="mb-6">
        <CardBody>
          <form onSubmit={upload} className="flex flex-wrap items-end gap-3">
            <div className="min-w-[16rem] flex-1">
              <Label htmlFor="file">File</Label>
              <Input
                id="file"
                type="file"
                onChange={(e) => setFile(e.target.files?.[0] ?? null)}
                accept=".pdf,.txt,.md,.doc,.docx,.csv,.xlsx,.xls,.png,.jpg,.jpeg,.webp,.gif,.mp3,.wav,.m4a,.flac,.ogg,.mp4,.mov,.webm"
              />
            </div>
            <div>
              <Label htmlFor="dept">Department</Label>
              <Select
                id="dept"
                value={uploadDept}
                onChange={(e) => setUploadDept(e.target.value as Department | "")}
              >
                <option value="">Unassigned</option>
                {DEPARTMENTS.map((d) => (
                  <option key={d} value={d}>
                    {d}
                  </option>
                ))}
              </Select>
            </div>
            <Button type="submit" disabled={!file || uploading}>
              {uploading ? "Uploading…" : "Upload"}
            </Button>
          </form>

          {notice ? <p className="mt-3 text-xs text-ok">{notice}</p> : null}
          <p className="mt-3 text-xs text-muted">
            Uploads are limited to 5 per minute and 50 MB per file. File type is verified by
            content, not just by extension.
          </p>
        </CardBody>
      </Card>

      {error ? (
        <div className="mb-4">
          <ErrorState message={error} onRetry={() => setError(null)} />
        </div>
      ) : null}

      {/* ---- list -------------------------------------------------------- */}
      <Card>
        <CardBody className="flex items-center gap-3 border-b border-border">
          <Label htmlFor="filter" className="mb-0">
            Filter
          </Label>
          <Select
            id="filter"
            className="w-auto"
            value={filter}
            onChange={(e) => setFilter(e.target.value as Department | "")}
          >
            <option value="">All departments</option>
            {DEPARTMENTS.map((d) => (
              <option key={d} value={d}>
                {d}
              </option>
            ))}
          </Select>
          <span className="ml-auto text-xs text-muted">{docs.data?.total ?? 0} documents</span>
        </CardBody>

        {docs.loading ? (
          <CardBody className="space-y-2">
            {Array.from({ length: 6 }).map((_, i) => (
              <Skeleton key={i} className="h-9" />
            ))}
          </CardBody>
        ) : docs.error ? (
          <CardBody>
            <ErrorState message={docs.error} onRetry={docs.reload} />
          </CardBody>
        ) : docs.data!.items.length === 0 ? (
          <EmptyState
            title="No documents here yet"
            hint="Upload a file above to add it to the knowledge base."
          />
        ) : (
          <Table>
            <thead>
              <tr>
                <Th>Name</Th>
                <Th>Type</Th>
                <Th>Department</Th>
                <Th>Status</Th>
                <Th className="text-right">Chunks</Th>
                <Th className="text-right">Size</Th>
                <Th />
              </tr>
            </thead>
            <tbody>
              {docs.data!.items.map((doc) => (
                <tr key={doc.id}>
                  <Td className="max-w-[20rem] truncate">{doc.name}</Td>
                  <Td className="text-muted">{doc.modality}</Td>
                  <Td className="capitalize text-muted">{doc.department ?? "—"}</Td>
                  <Td>
                    <Badge tone={statusTone(doc.status)}>{doc.status}</Badge>
                  </Td>
                  <Td className="text-right tabular-nums">{doc.chunk_count}</Td>
                  <Td className="text-right tabular-nums text-muted">
                    {formatBytes(doc.size_bytes)}
                  </Td>
                  <Td className="text-right">
                    <Button
                      variant="ghost"
                      size="sm"
                      onClick={() => remove(doc.id, doc.name)}
                      aria-label={`Delete ${doc.name}`}
                    >
                      Delete
                    </Button>
                  </Td>
                </tr>
              ))}
            </tbody>
          </Table>
        )}
      </Card>
    </>
  );
}
