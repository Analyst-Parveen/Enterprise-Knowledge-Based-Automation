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
  EmptyState,
  ErrorState,
  Select,
  Textarea,
} from "@/components/ui";
import { api } from "@/lib/api";
import { DEPARTMENTS, type ChatResponse, type Department } from "@/types/api";

interface Turn {
  question: string;
  response: ChatResponse;
}

export default function ChatPage() {
  const [question, setQuestion] = React.useState("");
  const [department, setDepartment] = React.useState<Department | "">("");
  const [turns, setTurns] = React.useState<Turn[]>([]);
  const [busy, setBusy] = React.useState(false);
  const [error, setError] = React.useState<string | null>(null);
  const [conversationId, setConversationId] = React.useState<string | null>(null);

  async function ask(event?: React.FormEvent) {
    event?.preventDefault();
    const asked = question.trim();
    if (!asked || busy) return;

    setBusy(true);
    setError(null);
    try {
      const response = await api.chat({
        question: asked,
        conversation_id: conversationId,
        department: department || null,
      });
      setTurns((prev) => [...prev, { question: asked, response }]);
      setConversationId(response.conversation_id);
      setQuestion("");
    } catch (err) {
      setError(err instanceof Error ? err.message : "The request failed.");
    } finally {
      setBusy(false);
    }
  }

  return (
    <>
      <PageHeader
        title="Knowledge Chat"
        description="Answers are drawn only from documents in your tenant, and every claim is cited."
      />

      <div className="grid gap-6 lg:grid-cols-3">
        <div className="space-y-4 lg:col-span-2">
          {turns.length === 0 && !busy ? (
            <Card>
              <EmptyState
                title="Ask your first question"
                hint="Try: “What is the domestic hotel reimbursement limit?” If the answer is not in your knowledge base, the assistant will say so rather than guess."
              />
            </Card>
          ) : null}

          {turns.map((turn, index) => (
            <Card key={index}>
              <CardHeader>
                <CardTitle>{turn.question}</CardTitle>
              </CardHeader>
              <CardBody className="space-y-4">
                {/* Model output rendered as TEXT, never as HTML. */}
                <p className="whitespace-pre-wrap text-sm leading-relaxed text-fg">
                  {turn.response.answer}
                </p>

                {turn.response.citations.length > 0 ? (
                  <div>
                    <p className="mb-1.5 text-xs font-medium text-muted">Citations</p>
                    <ul className="space-y-1">
                      {turn.response.citations.map((citation) => (
                        <li
                          key={`${citation.source_number}-${citation.chunk_id}`}
                          className="flex flex-wrap items-center gap-2 rounded-md bg-bg px-2 py-1.5 text-xs"
                        >
                          <Badge tone="accent">S{citation.source_number}</Badge>
                          <span className="truncate text-fg">{citation.document_name}</span>
                          {citation.page_number ? (
                            <span className="text-muted">p.{citation.page_number}</span>
                          ) : null}
                          {citation.section ? (
                            <span className="text-muted">§ {citation.section}</span>
                          ) : null}
                        </li>
                      ))}
                    </ul>
                  </div>
                ) : (
                  <p className="text-xs text-warn">
                    No citations were returned for this answer.
                  </p>
                )}

                {/* The full response envelope, visible - this is the point of the product. */}
                <div className="flex flex-wrap items-center gap-x-4 gap-y-2 border-t border-border pt-3 text-xs text-muted">
                  <Confidence value={turn.response.confidence} />
                  <span className="font-mono">{turn.response.model_used}</span>
                  <span>{turn.response.latency_ms} ms</span>
                  <span>
                    {turn.response.input_tokens}/{turn.response.output_tokens} tok
                  </span>
                  <span>${turn.response.estimated_cost.toFixed(6)}</span>
                  {turn.response.cache_hit ? <Badge tone="ok">cached</Badge> : null}
                  <span className="font-mono opacity-60">
                    {turn.response.correlation_id.slice(0, 8)}
                  </span>
                </div>
              </CardBody>
            </Card>
          ))}

          {busy ? (
            <Card>
              <CardBody>
                <p className="text-sm text-muted">Searching your knowledge base…</p>
              </CardBody>
            </Card>
          ) : null}

          {error ? <ErrorState message={error} onRetry={() => setError(null)} /> : null}

          <Card>
            <CardBody>
              <form onSubmit={ask} className="space-y-3">
                <Textarea
                  rows={3}
                  value={question}
                  onChange={(e) => setQuestion(e.target.value)}
                  onKeyDown={(e) => {
                    if (e.key === "Enter" && (e.metaKey || e.ctrlKey)) void ask();
                  }}
                  placeholder="Ask about a policy, SOP, spreadsheet or recording…"
                  aria-label="Your question"
                  disabled={busy}
                />
                <div className="flex flex-wrap items-center gap-2">
                  <Select
                    aria-label="Department filter"
                    className="w-auto"
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
                  <VoiceInput onTranscript={(text) => setQuestion((q) => `${q} ${text}`.trim())} />
                  <Button type="submit" disabled={busy || !question.trim()} className="ml-auto">
                    {busy ? "Asking…" : "Ask"}
                  </Button>
                </div>
              </form>
            </CardBody>
          </Card>
        </div>

        {/* ---- retrieved chunks ------------------------------------------ */}
        <Card className="h-fit">
          <CardHeader>
            <CardTitle>Retrieved context</CardTitle>
          </CardHeader>
          <CardBody>
            {turns.length === 0 ? (
              <p className="text-xs text-muted">
                The chunks used to answer your question appear here, with their relevance scores.
              </p>
            ) : (
              <ul className="space-y-2">
                {turns[turns.length - 1].response.retrieved_chunks.map((chunk) => (
                  <li key={chunk.chunk_id} className="rounded-md bg-bg p-2 text-xs">
                    <div className="flex items-center justify-between gap-2">
                      <span className="truncate font-medium text-fg">{chunk.document_name}</span>
                      <span className="tabular-nums text-muted">{chunk.score.toFixed(3)}</span>
                    </div>
                    <p className="mt-1 line-clamp-3 text-muted">{chunk.preview}</p>
                  </li>
                ))}
              </ul>
            )}
          </CardBody>
        </Card>
      </div>
    </>
  );
}

/**
 * Voice input via the browser's SpeechRecognition where available.
 *
 * Note: this is the browser's own transcription, used only to fill the text
 * box. Uploaded audio and video files are transcribed server-side by Amazon
 * Transcribe through the normal ingestion pipeline - there is no separate AI
 * backend for voice. See PROJECT.md section 13.
 */
function VoiceInput({ onTranscript }: { onTranscript: (text: string) => void }) {
  const [listening, setListening] = React.useState(false);
  const [supported, setSupported] = React.useState(false);
  const recognitionRef = React.useRef<any>(null);

  React.useEffect(() => {
    const Recognition =
      (window as any).SpeechRecognition ?? (window as any).webkitSpeechRecognition;
    setSupported(Boolean(Recognition));
  }, []);

  function toggle() {
    const Recognition =
      (window as any).SpeechRecognition ?? (window as any).webkitSpeechRecognition;
    if (!Recognition) return;

    if (listening) {
      recognitionRef.current?.stop();
      setListening(false);
      return;
    }

    const recognition = new Recognition();
    recognition.lang = "en-US";
    recognition.interimResults = false;
    recognition.onresult = (event: any) => {
      onTranscript(event.results[0][0].transcript as string);
    };
    recognition.onend = () => setListening(false);
    recognition.onerror = () => setListening(false);
    recognition.start();

    recognitionRef.current = recognition;
    setListening(true);
  }

  if (!supported) return null;

  return (
    <Button
      type="button"
      variant={listening ? "danger" : "secondary"}
      onClick={toggle}
      aria-pressed={listening}
    >
      {listening ? "Stop" : "Speak"}
    </Button>
  );
}
