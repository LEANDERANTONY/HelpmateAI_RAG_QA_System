"use client";

/**
 * FeedbackButtons — inline thumb-up / thumb-down with optional
 * follow-up comment, rendered under each /qa answer card.
 *
 * State machine (lazy-commit to avoid double-inserting rows):
 *
 *   idle  ── thumb click ──▶  rated-pending  ── send / skip ──▶  submitting  ──▶  submitted
 *                                     │
 *                                     └── auto-commit timer (8s) ──▶  submitting
 *
 *   In rated-pending we hold the rating locally; submitFeedback is
 *   called EXACTLY ONCE per user action (either with the comment, or
 *   rating-only after the user dismisses the comment textarea or the
 *   timer fires). Codex flagged on PR #5 that the previous design
 *   double-inserted: one row on thumb click, a second row on comment
 *   submit — doublecounting in any rating-rate analytics query.
 *
 *   The auto-commit timer is also the safety net for the "user taps
 *   thumb and walks away" case — without it, lazy commit would lose
 *   the rating. 8 seconds is long enough that a thoughtful comment
 *   has time to start typing (typing resets the timer), short enough
 *   that idle users still get their rating captured.
 *
 *   On component unmount mid-rated-pending we fire one final best-
 *   effort submit so closing the browser tab during the timer
 *   window doesn't silently drop the rating.
 *
 * Accessibility:
 *   * Each button has aria-label + aria-pressed reflecting the
 *     current selection. Screen readers announce the pair as a
 *     togglable group.
 *   * The "Thanks!" status uses role="status" with aria-live="polite"
 *     so it's announced without interrupting whatever the user is
 *     reading.
 *   * The textarea is associated to its label via a useId-derived id
 *     so multiple FeedbackButtons mounted on the same page (one per
 *     answer card) don't collide on DOM ids.
 */

import { useCallback, useEffect, useId, useRef, useState } from "react";

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
  | { kind: "rated-pending"; rating: FeedbackRating }
  | { kind: "submitting"; rating: FeedbackRating }
  | { kind: "submitted"; rating: FeedbackRating };

const COMMENT_MAX_CHARS = 2000;
/** How long to wait after the last user activity in rated-pending
 *  before auto-committing the rating-only feedback. Tuned to give a
 *  thoughtful user time to start typing in the comment box (typing
 *  resets the timer) while still capturing the rating from users
 *  who tap and walk away. */
const AUTO_COMMIT_MS = 8000;

export function FeedbackButtons({
  surface = "answer",
  traceId = null,
  className,
  onError,
}: FeedbackButtonsProps) {
  const [state, setState] = useState<FeedbackState>({ kind: "idle" });
  const [comment, setComment] = useState<string>("");
  const [errorMessage, setErrorMessage] = useState<string | null>(null);
  // useId guarantees a DOM-unique value per component instance — the
  // label → textarea association would otherwise collide whenever
  // multiple answer cards mount on the same page.
  const commentTextareaId = useId();

  // Refs for the auto-commit timer + unmount best-effort submit.
  // Using refs (not state) so changes to the timer don't trigger
  // re-renders, and so the cleanup useEffect can read the LATEST
  // rating/comment instead of the snapshot at the moment the effect
  // first ran.
  const timerRef = useRef<ReturnType<typeof setTimeout> | null>(null);
  const stateRef = useRef<FeedbackState>(state);
  const commentRef = useRef<string>(comment);
  stateRef.current = state;
  commentRef.current = comment;
  // Belt-and-suspenders against the unmount race: setState is async,
  // so between a Send/Skip click and the next render, stateRef still
  // says "rated-pending" — and an unmount during that window would
  // fire a second submitFeedback. ``committingRef`` flips true the
  // instant any commit path starts; the unmount effect bails out if
  // it's set. Mirrors the AI Job Agent fix flagged by Codex P2.
  const committingRef = useRef<boolean>(false);

  const clearTimer = useCallback(() => {
    if (timerRef.current !== null) {
      clearTimeout(timerRef.current);
      timerRef.current = null;
    }
  }, []);

  /** The single submit point. Sends EXACTLY ONE row per user action,
   *  whether triggered by the Send button, the Skip button, the
   *  auto-commit timer, or the unmount cleanup. */
  const commitFeedback = useCallback(
    async (rating: FeedbackRating, commentText: string) => {
      clearTimer();
      // Mark in-flight synchronously BEFORE the async setState. The
      // unmount effect reads this to decide whether to fire its own
      // best-effort flush — without the flag, an unmount between the
      // Send click and the next render would double-submit.
      committingRef.current = true;
      // Mark in-flight so the buttons disable + a second commit can't
      // race in (e.g. Send button click during the auto-commit timer
      // firing).
      setState({ kind: "submitting", rating });
      setErrorMessage(null);
      try {
        await submitFeedback({
          rating,
          traceId,
          surface,
          comment: commentText.slice(0, COMMENT_MAX_CHARS),
        });
        setState({ kind: "submitted", rating });
      } catch (error) {
        // On failure we revert to rated-pending so the user can
        // retry — losing the rating because of a transient network
        // hiccup would be a worse UX than asking them to retry.
        setState({ kind: "rated-pending", rating });
        const message =
          error instanceof Error
            ? error.message
            : "Couldn't save your feedback. Please try again.";
        setErrorMessage(message);
        onError?.(message);
      }
    },
    [clearTimer, onError, surface, traceId],
  );

  const scheduleAutoCommit = useCallback(
    (rating: FeedbackRating) => {
      clearTimer();
      timerRef.current = setTimeout(() => {
        // Re-read state from the ref so a state change that happened
        // after scheduling (e.g. the user clicked Send manually)
        // doesn't double-fire commitFeedback.
        if (stateRef.current.kind !== "rated-pending") return;
        void commitFeedback(rating, commentRef.current);
      }, AUTO_COMMIT_MS);
    },
    [clearTimer, commitFeedback],
  );

  // Best-effort flush on unmount: if the user navigated away while
  // a rating is still pending AND no commit path has started, fire
  // a final submitFeedback so the rating isn't silently dropped.
  // The committingRef guard prevents the race where Send/Skip
  // already started a commit but state hasn't re-rendered yet.
  useEffect(() => {
    return () => {
      clearTimer();
      const current = stateRef.current;
      if (current.kind === "rated-pending" && !committingRef.current) {
        // Fire-and-forget — no UI to update after unmount and the
        // backend gracefully handles the case where the user closes
        // the tab mid-request.
        void submitFeedback({
          rating: current.rating,
          traceId,
          surface,
          comment: commentRef.current.slice(0, COMMENT_MAX_CHARS),
        }).catch(() => {
          // Unmount path: nothing to do with the error. The user is
          // already gone.
        });
      }
    };
  }, [clearTimer, surface, traceId]);

  function handleRatingClick(rating: FeedbackRating) {
    if (state.kind === "submitting") return;
    setErrorMessage(null);

    if (state.kind === "rated-pending" && state.rating === rating) {
      // Toggling off — go back to idle without ever submitting.
      // This is the un-rate-before-commit path; analytics never see
      // a row for it. (Once committed the rating can no longer be
      // cleared from the client — backend has no DELETE policy.)
      clearTimer();
      setState({ kind: "idle" });
      setComment("");
      return;
    }
    if (state.kind === "submitted" && state.rating === rating) {
      // Already committed; a same-rating click is a no-op. We
      // intentionally do NOT toggle off here because the backend
      // row already exists — clearing the UI state without removing
      // the row would be misleading.
      return;
    }
    // Either fresh rating from idle, or switching ratings while
    // pending, or starting a new rating after a previous submit.
    // In all cases we land in rated-pending with the new rating +
    // restart the auto-commit timer.
    //
    // Clear any leftover comment text: a comment typed under the
    // PRIOR rating shouldn't auto-submit with the new rating.
    // Without this, a user could type "wrong tone" under 👎, switch
    // to 👍, and the 8s auto-commit fires with rating="up" +
    // stale "wrong tone" comment. Codex P2 on PR #5.
    setComment("");
    setState({ kind: "rated-pending", rating });
    scheduleAutoCommit(rating);
  }

  function handleCommentChange(value: string) {
    setComment(value);
    // User is engaging with the comment — push the auto-commit
    // timer out so they have time to finish typing.
    if (state.kind === "rated-pending") {
      scheduleAutoCommit(state.rating);
    }
  }

  function handleSend() {
    if (state.kind !== "rated-pending") return;
    void commitFeedback(state.rating, comment.trim());
  }

  function handleSkipComment() {
    if (state.kind !== "rated-pending") return;
    void commitFeedback(state.rating, "");
  }

  const upPressed =
    (state.kind === "rated-pending" ||
      state.kind === "submitting" ||
      state.kind === "submitted") &&
    state.rating === "up";
  const downPressed =
    (state.kind === "rated-pending" ||
      state.kind === "submitting" ||
      state.kind === "submitted") &&
    state.rating === "down";
  const buttonsDisabled = state.kind === "submitting";
  const showCommentArea = state.kind === "rated-pending";
  const showSubmittedStatus = state.kind === "submitted";

  return (
    <div
      className={className ? `h-feedback ${className}` : "h-feedback"}
      data-feedback-surface={surface}
      data-feedback-state={state.kind}
    >
      <div className="h-feedback-buttons">
        <button
          aria-label="Helpful"
          aria-pressed={upPressed}
          className={`h-feedback-btn${upPressed ? " active" : ""}`}
          disabled={buttonsDisabled}
          onClick={() => handleRatingClick("up")}
          type="button"
        >
          <span aria-hidden>👍</span>
        </button>
        <button
          aria-label="Not helpful"
          aria-pressed={downPressed}
          className={`h-feedback-btn${downPressed ? " active" : ""}`}
          disabled={buttonsDisabled}
          onClick={() => handleRatingClick("down")}
          type="button"
        >
          <span aria-hidden>👎</span>
        </button>
        {showSubmittedStatus ? (
          <span aria-live="polite" className="h-feedback-status" role="status">
            Thanks!
          </span>
        ) : null}
      </div>

      {showCommentArea ? (
        <div className="h-feedback-comment">
          <label htmlFor={commentTextareaId} className="h-feedback-comment-label">
            Want to tell us more? (optional)
          </label>
          <textarea
            className="h-feedback-comment-input"
            disabled={buttonsDisabled}
            id={commentTextareaId}
            maxLength={COMMENT_MAX_CHARS}
            onChange={(event) => handleCommentChange(event.target.value)}
            placeholder={
              state.kind === "rated-pending" && state.rating === "up"
                ? "What worked? (e.g. citations, tone, clarity)"
                : "What missed? (e.g. wrong tone, missing facts, hallucination)"
            }
            rows={2}
            value={comment}
          />
          <div className="h-feedback-comment-row">
            <button
              className="h-btn h-btn-primary h-feedback-comment-submit"
              disabled={buttonsDisabled}
              onClick={handleSend}
              type="button"
            >
              Send
            </button>
            <button
              className="h-btn h-btn-ghost h-feedback-comment-skip"
              disabled={buttonsDisabled}
              onClick={handleSkipComment}
              type="button"
            >
              Skip
            </button>
            {errorMessage ? (
              <span className="h-feedback-error" role="alert">
                {errorMessage}
              </span>
            ) : null}
          </div>
        </div>
      ) : null}
    </div>
  );
}
