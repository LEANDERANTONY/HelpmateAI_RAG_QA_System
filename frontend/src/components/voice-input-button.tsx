"use client";

/**
 * VoiceInputButton — push-to-talk mic that records a short audio
 * clip, sends it to /transcribe, and hands the transcript back to
 * the parent (typically appended to the ask textarea).
 *
 * UX:
 *   * Idle state: shows a mic icon; tap to start recording.
 *   * Recording state: button shows the recording chrome (red dot
 *     + pulse) and tap again to stop. While recording the parent
 *     should still be interactive — the mic doesn't block the
 *     textarea, so a user can edit existing text while dictating
 *     a continuation (the transcript is APPENDED, not replaced).
 *   * Transcribing state: button shows a spinner while the server
 *     does its Whisper round-trip. Tapping again is a no-op.
 *
 * Failure modes:
 *   * Browser doesn't support MediaRecorder → button is disabled
 *     and `isVoiceInputSupported()` returns false (the parent
 *     should not mount this component at all in that case).
 *   * Mic permission denied → catch path raises a clear,
 *     actionable error string via `onError`. The component returns
 *     to idle so the user can retry after fixing the permission.
 *   * Transcribe call fails (network / 502 / etc.) → same path.
 *   * /transcribe not configured (server 503) → same.
 *
 * Cleanup: the mic stream is stopped in all exit paths (success,
 * error, unmount) so the OS-level mic indicator never lingers.
 */

import { useEffect, useRef, useState } from "react";

import { transcribeAudio } from "@/lib/api";

export type VoiceInputButtonProps = {
  /** Called with the transcribed text after Whisper returns. The
   *  parent typically appends this to the ask textarea contents
   *  rather than replacing them, so a user can mix typing + voice. */
  onTranscript: (text: string) => void;
  /** Optional error sink — the parent toast layer typically routes
   *  failures here for a "Couldn't record audio" message. */
  onError?: (message: string) => void;
  /** Optional disabled gate from the parent (typically while a /qa
   *  request is streaming and the textarea is locked). */
  disabled?: boolean;
  /** Optional className for the outer button. Defaults to the
   *  HelpmateAI inline mic chrome. */
  className?: string;
};

type RecorderState =
  | "idle"
  | "requesting"
  | "recording"
  | "transcribing";

const MIME_CANDIDATES = [
  "audio/webm;codecs=opus",
  "audio/webm",
  "audio/mp4",
  "audio/ogg;codecs=opus",
] as const;

export function isVoiceInputSupported(): boolean {
  if (typeof window === "undefined") return false;
  if (!navigator.mediaDevices || !navigator.mediaDevices.getUserMedia) return false;
  if (typeof window.MediaRecorder === "undefined") return false;
  return true;
}

function pickRecorderMimeType(): string {
  if (typeof window === "undefined") return "";
  const recorder = window.MediaRecorder;
  if (!recorder || typeof recorder.isTypeSupported !== "function") return "";
  for (const candidate of MIME_CANDIDATES) {
    if (recorder.isTypeSupported(candidate)) {
      return candidate;
    }
  }
  return "";
}

export function VoiceInputButton({
  onTranscript,
  onError,
  disabled,
  className,
}: VoiceInputButtonProps) {
  const [state, setState] = useState<RecorderState>("idle");
  const streamRef = useRef<MediaStream | null>(null);
  const recorderRef = useRef<MediaRecorder | null>(null);
  const chunksRef = useRef<Blob[]>([]);
  const mimeRef = useRef<string>("");
  // Mirror onTranscript/onError into a ref so the MediaRecorder
  // "stop" event listener — registered once at recording start —
  // reads the LATEST callbacks at firing time, not the snapshot
  // from when the recorder was constructed. Without this, a parent
  // re-render with new callback identities between start and stop
  // would have the stop handler invoke stale closures. Mirrors the
  // AI Job Agent fix flagged by Codex P2 on PR #3 round 5.
  const propsRef = useRef({ onTranscript, onError });
  propsRef.current = { onTranscript, onError };

  // Stop the mic + recorder when the component unmounts mid-recording.
  // Without this, navigating away while recording leaves the OS mic
  // indicator on until the next page interaction.
  useEffect(() => {
    return () => {
      const recorder = recorderRef.current;
      if (recorder && recorder.state !== "inactive") {
        try {
          recorder.stop();
        } catch {
          // already stopped or invalid state — fine to ignore
        }
      }
      const stream = streamRef.current;
      if (stream) {
        stream.getTracks().forEach((track) => track.stop());
        streamRef.current = null;
      }
    };
  }, []);

  async function startRecording() {
    if (!isVoiceInputSupported()) {
      onError?.(
        "Voice input isn't supported in this browser. Try Chrome, Firefox, or Safari.",
      );
      return;
    }
    setState("requesting");
    try {
      const stream = await navigator.mediaDevices.getUserMedia({ audio: true });
      streamRef.current = stream;
      const mime = pickRecorderMimeType();
      const recorder = mime
        ? new MediaRecorder(stream, { mimeType: mime })
        : new MediaRecorder(stream);
      // After construction, the recorder knows what container it
      // settled on. Prefer that over a hardcoded fallback so the
      // empty-mime path doesn't mislabel non-webm blobs (Safari often
      // produces mp4 here).
      mimeRef.current = mime || recorder.mimeType || "audio/webm";
      recorderRef.current = recorder;
      chunksRef.current = [];
      recorder.addEventListener("dataavailable", (event) => {
        if (event.data && event.data.size > 0) {
          chunksRef.current.push(event.data);
        }
      });
      recorder.addEventListener("stop", handleRecorderStop);
      recorder.start();
      setState("recording");
    } catch (error) {
      setState("idle");
      // Clean up any allocated mic stream so the OS-level mic
      // indicator turns off. Without this, if getUserMedia succeeded
      // but MediaRecorder construction failed, the tracks stay live.
      if (streamRef.current) {
        streamRef.current.getTracks().forEach((track) => track.stop());
        streamRef.current = null;
      }
      recorderRef.current = null;
      const message =
        error instanceof Error && error.name === "NotAllowedError"
          ? "Microphone permission was denied. Update your browser settings to allow audio capture, then try again."
          : "Couldn't start the microphone. Check that your device has a working mic and try again.";
      onError?.(message);
    }
  }

  function stopRecording() {
    const recorder = recorderRef.current;
    if (!recorder) return;
    if (recorder.state === "recording") {
      recorder.stop();
    }
  }

  async function handleRecorderStop() {
    // Tear down the mic stream before the POST — the recording is
    // already complete and the user shouldn't see the OS mic
    // indicator while we're waiting on Whisper.
    if (streamRef.current) {
      streamRef.current.getTracks().forEach((track) => track.stop());
      streamRef.current = null;
    }
    const chunks = chunksRef.current;
    chunksRef.current = [];
    recorderRef.current = null;
    if (chunks.length === 0) {
      setState("idle");
      return;
    }
    setState("transcribing");
    const blob = new Blob(chunks, {
      type: mimeRef.current || "audio/webm",
    });
    // Read the LATEST callbacks via propsRef so a parent re-render
    // mid-recording doesn't leave us calling stale closure values.
    const currentProps = propsRef.current;
    try {
      const response = await transcribeAudio(blob);
      const text = (response.text || "").trim();
      if (text) {
        currentProps.onTranscript(text);
      }
    } catch (error) {
      const message =
        error instanceof Error
          ? error.message
          : "Couldn't transcribe the audio. Please try again.";
      currentProps.onError?.(message);
    } finally {
      setState("idle");
    }
  }

  function handleClick() {
    if (disabled) return;
    if (state === "idle") {
      void startRecording();
    } else if (state === "recording") {
      stopRecording();
    }
    // requesting / transcribing → no-op (button shows a spinner so
    // the user knows their tap was received).
  }

  const isBusy = state === "requesting" || state === "transcribing";
  const isRecording = state === "recording";
  const label = isRecording
    ? "Stop recording"
    : isBusy
      ? "Transcribing"
      : "Start voice input";

  return (
    <button
      aria-label={label}
      aria-pressed={isRecording}
      className={`${className ?? "h-voice-btn"}${isRecording ? " recording" : ""}`}
      disabled={Boolean(disabled) || isBusy}
      onClick={handleClick}
      title={label}
      type="button"
    >
      {state === "transcribing" ? (
        <span aria-hidden className="button-spinner" />
      ) : isRecording ? (
        <span aria-hidden className="h-voice-dot" />
      ) : (
        <span aria-hidden>🎤</span>
      )}
    </button>
  );
}
