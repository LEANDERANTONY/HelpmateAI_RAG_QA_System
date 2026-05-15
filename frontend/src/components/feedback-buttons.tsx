"use client";

/**
 * FeedbackButtons — inline thumb-up / thumb-down with optional
 * follow-up comment, rendered under each /qa answer card.
 *
 * Flow:
 *   1. User taps 👍 or 👎. The button posts a feedback row immediately
 *      (optimistic UI: the state flips to "submitted" before the
 *      network completes). Failure reverts the state and surfaces an
 *      inline error string the parent toast layer can pick up.
 *   2. After rating, an optional "Want to tell us more?" textarea
 *      appears. Submitting that PATCHes a fresh row with the same
 *      trace_id (the backend de-dupes on (user_id, trace_id, created_at)
 *      so the latest comment wins for analytics — see feedback_store.py).
 *
 * Accessibility:
 *   * Each button has aria-label + aria-pressed reflecting the
 *     current rating selection. Screen readers announce the pair as
 *     a togglable group.
 *   * The "Thanks!" status uses role="status" with aria-live="polite"
 *     so it's announced without interrupting whatever the user is
 *     reading.
 *   * The textarea is associated to its label via a useId-derived id
 *     so multiple FeedbackButtons mounted on the same page (one per
 *     answer card) don't collide on DOM ids.
 */

import { useId, useState } from "react";

import {
  submitFeedback,
  type FeedbackRating,
  type FeedbackSurface,
} from "@/lib/api";

export type FeedbackButtonsProps = {
  /** Which surface this feedback applies to. Defaults to "answer". */
  surface?: FeedbackSurface;
  /** Optional trace_id from the /qa run trace. When present, the row
   *  joins to helpmate_run_traces for the model × cost × rating
   *  rollup. Pass null for surfaces without a single trace. */
  traceId?: string | null;
  /** Optional className to forward to the outer container. Defaults
   *  to a compact inline layout that fits inside an answer card. */
  className?: string;
  /** Optional error sink — the parent toast layer typically routes
   *  failures here for a "Couldn't save feedback" message. */
  onError?: (message: string) => void;
};

type FeedbackState =
  | { kind: "idle" }
  | { kind: "submitting"; rating: FeedbackRating }
  | { kind: "submitted"; rating: FeedbackRating };

const COMMENT_MAX_CHARS = 2000;

export function FeedbackButtons({
  surface = "answer",
  traceId = null,
  className,
  onError,
}: FeedbackButtonsProps) {
  const [state, setState] = useState<FeedbackState>({ kind: "idle" });
  const [comment, setComment] = useState<string>("");
  const [commentSubmitting, setCommentSubmitting] = useState<boolean>(false);
  const [commentSubmitted, setCommentSubmitted] = useState<boolean>(false);
  const [commentError, setCommentError] = useState<string | null>(null);
  // useId guarantees a DOM-unique value per component instance — the
  // label → textarea association would otherwise collide whenever
  // multiple answer cards mount on the same page.
  const commentTextareaId = useId();

  const currentRating: FeedbackRating | null =
    state.kind === "idle" ? null : state.rating;
  const hasRating = currentRating !== null;
  const isSubmittingRating = state.kind === "submitting";

  async function handleRatingClick(rating: FeedbackRating) {
    if (isSubmittingRating) return;
    // Allow toggling: tap the same rating again to clear it. This
    // mirrors the YouTube-style "I changed my mind" affordance.
    if (currentRating === rating) {
      setState({ kind: "idle" });
      setComment("");
      setCommentSubmitted(false);
      setCommentError(null);
      return;
    }
    setState({ kind: "submitting", rating });
    try {
      await submitFeedback({
        rating,
        traceId,
        surface,
        // The initial rating tap doesn't carry a comment — the
        // user submits that separately if they want to.
        comment: "",
      });
      setState({ kind: "submitted", rating });
    } catch (error) {
      setState({ kind: "idle" });
      const message =
        error instanceof Error
          ? error.message
          : "Couldn't record your feedback. Please try again.";
      onError?.(message);
    }
  }

  async function handleCommentSubmit() {
    const trimmed = comment.trim();
    if (!trimmed || !currentRating) return;
    setCommentSubmitting(true);
    setCommentError(null);
    try {
      await submitFeedback({
        rating: currentRating,
        traceId,
        surface,
        comment: trimmed.slice(0, COMMENT_MAX_CHARS),
      });
      setCommentSubmitted(true);
      setComment("");
    } catch (error) {
      const message =
        error instanceof Error
          ? error.message
          : "Couldn't save your comment. Please try again.";
      setCommentError(message);
      onError?.(message);
    } finally {
      setCommentSubmitting(false);
    }
  }

  const upPressed = currentRating === "up";
  const downPressed = currentRating === "down";

  return (
    <div
      className={className ? `h-feedback ${className}` : "h-feedback"}
      data-feedback-surface={surface}
    >
      <div className="h-feedback-buttons">
        <button
          aria-label="Helpful"
          aria-pressed={upPressed}
          className={`h-feedback-btn${upPressed ? " active" : ""}`}
          disabled={isSubmittingRating}
          onClick={() => handleRatingClick("up")}
          type="button"
        >
          <span aria-hidden>👍</span>
        </button>
        <button
          aria-label="Not helpful"
          aria-pressed={downPressed}
          className={`h-feedback-btn${downPressed ? " active" : ""}`}
          disabled={isSubmittingRating}
          onClick={() => handleRatingClick("down")}
          type="button"
        >
          <span aria-hidden>👎</span>
        </button>
        {state.kind === "submitted" && !commentSubmitted ? (
          <span aria-live="polite" className="h-feedback-status" role="status">
            Thanks!
          </span>
        ) : null}
        {commentSubmitted ? (
          <span aria-live="polite" className="h-feedback-status" role="status">
            Comment saved.
          </span>
        ) : null}
      </div>

      {hasRating && !commentSubmitted ? (
        <div className="h-feedback-comment">
          <label htmlFor={commentTextareaId} className="h-feedback-comment-label">
            Want to tell us more? (optional)
          </label>
          <textarea
            className="h-feedback-comment-input"
            disabled={commentSubmitting}
            id={commentTextareaId}
            maxLength={COMMENT_MAX_CHARS}
            onChange={(event) => setComment(event.target.value)}
            placeholder={
              currentRating === "up"
                ? "What worked? (e.g. citations, tone, clarity)"
                : "What missed? (e.g. wrong tone, missing facts, hallucination)"
            }
            rows={2}
            value={comment}
          />
          <div className="h-feedback-comment-row">
            <button
              className="h-btn h-btn-ghost h-feedback-comment-submit"
              disabled={commentSubmitting || !comment.trim()}
              onClick={handleCommentSubmit}
              type="button"
            >
              {commentSubmitting ? "Saving…" : "Send"}
            </button>
            {commentError ? (
              <span className="h-feedback-error" role="alert">
                {commentError}
              </span>
            ) : null}
          </div>
        </div>
      ) : null}
    </div>
  );
}
