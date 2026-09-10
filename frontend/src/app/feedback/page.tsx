"use client";

import * as React from "react";

import { PageHeader } from "@/components/shell";
import {
  Button,
  Card,
  CardBody,
  CardHeader,
  CardTitle,
  ErrorState,
  Label,
  Select,
  Textarea,
} from "@/components/ui";
import { api } from "@/lib/api";

const REASONS = [
  { value: "", label: "General comment" },
  { value: "missing_citation", label: "Answer had no citation" },
  { value: "wrong_answer", label: "Answer was wrong" },
  { value: "wrong_document", label: "Cited the wrong document" },
  { value: "too_slow", label: "Too slow" },
  { value: "refused_wrongly", label: "Refused a question it should answer" },
];

export default function FeedbackPage() {
  const [rating, setRating] = React.useState<1 | -1>(1);
  const [reason, setReason] = React.useState("");
  const [comment, setComment] = React.useState("");
  const [sent, setSent] = React.useState(false);
  const [busy, setBusy] = React.useState(false);
  const [error, setError] = React.useState<string | null>(null);

  async function submit(event: React.FormEvent) {
    event.preventDefault();
    setBusy(true);
    setError(null);
    try {
      await api.feedback({ rating, reason: reason || null, comment: comment || null });
      setSent(true);
      setComment("");
    } catch (err) {
      setError(err instanceof Error ? err.message : "Could not send feedback.");
    } finally {
      setBusy(false);
    }
  }

  return (
    <>
      <PageHeader
        title="Feedback"
        description="Tell us where the assistant got it wrong. Feedback is stored against your tenant and reviewed by admins."
      />

      <Card className="max-w-2xl">
        <CardHeader>
          <CardTitle>Share feedback</CardTitle>
        </CardHeader>
        <CardBody>
          {sent ? (
            <div className="space-y-3">
              <p className="text-sm text-ok">Thank you — your feedback was recorded.</p>
              <Button variant="secondary" onClick={() => setSent(false)}>
                Send more
              </Button>
            </div>
          ) : (
            <form onSubmit={submit} className="space-y-4">
              <div>
                <Label>Overall</Label>
                <div className="flex gap-2">
                  <Button
                    type="button"
                    variant={rating === 1 ? "primary" : "secondary"}
                    onClick={() => setRating(1)}
                    aria-pressed={rating === 1}
                  >
                    Helpful
                  </Button>
                  <Button
                    type="button"
                    variant={rating === -1 ? "danger" : "secondary"}
                    onClick={() => setRating(-1)}
                    aria-pressed={rating === -1}
                  >
                    Not helpful
                  </Button>
                </div>
              </div>

              <div>
                <Label htmlFor="reason">Reason</Label>
                <Select id="reason" value={reason} onChange={(e) => setReason(e.target.value)}>
                  {REASONS.map((r) => (
                    <option key={r.value} value={r.value}>
                      {r.label}
                    </option>
                  ))}
                </Select>
              </div>

              <div>
                <Label htmlFor="comment">Details</Label>
                <Textarea
                  id="comment"
                  rows={5}
                  value={comment}
                  onChange={(e) => setComment(e.target.value)}
                  placeholder="What did you ask, and what should the answer have been?"
                  maxLength={2000}
                />
              </div>

              {error ? <ErrorState message={error} /> : null}

              <Button type="submit" disabled={busy}>
                {busy ? "Sending…" : "Send feedback"}
              </Button>
            </form>
          )}
        </CardBody>
      </Card>
    </>
  );
}
