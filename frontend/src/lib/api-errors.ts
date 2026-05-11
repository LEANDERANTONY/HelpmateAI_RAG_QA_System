export type ApiOperation = "upload" | "index" | "ask" | "load" | "default";

export class ApiError extends Error {
  status: number;
  detail: string;
  retriable: boolean;
  retryAfterSeconds: number | null;

  constructor(
    status: number,
    detail: string,
    retriable: boolean,
    retryAfterSeconds: number | null = null,
  ) {
    super(detail || `Request failed (${status})`);
    this.name = "ApiError";
    this.status = status;
    this.detail = detail;
    this.retriable = retriable;
    this.retryAfterSeconds = retryAfterSeconds;
  }
}

export function isApiError(err: unknown): err is ApiError {
  return err instanceof ApiError;
}

export function isRetriableStatus(status: number) {
  return (
    status === 0 ||
    status === 408 ||
    status === 429 ||
    status === 500 ||
    status === 502 ||
    status === 503 ||
    status === 504
  );
}

export type ApiErrorCopy = {
  title: string;
  body: string;
  action: string | null;
};

function isOffline() {
  return typeof navigator !== "undefined" && navigator.onLine === false;
}

export function messageForApiError(
  err: unknown,
  op: ApiOperation = "default",
): ApiErrorCopy {
  if (!isApiError(err)) {
    if (isOffline()) {
      return {
        title: "You're offline",
        body: "Check your connection and try again.",
        action: "Try again",
      };
    }
    return {
      title: "Something went wrong",
      body: "Try again in a moment.",
      action: "Try again",
    };
  }

  const { status, detail, retryAfterSeconds } = err;

  if (status === 0) {
    if (isOffline()) {
      return {
        title: "You're offline",
        body: "Check your connection and try again.",
        action: "Try again",
      };
    }
    return {
      title: "Can't reach the server",
      body: "The server didn't respond. Try again.",
      action: "Try again",
    };
  }

  if (status === 401 || status === 403) {
    return {
      title: "Sign-in required",
      body: "Sign in with Google to continue.",
      action: "Sign in",
    };
  }

  if (status === 404) {
    switch (op) {
      case "ask":
      case "index":
        return {
          title: "Document not found",
          body: "It may have been removed. Upload a new one.",
          action: null,
        };
      case "load":
        return {
          title: "Workspace not found",
          body: "Upload a document to get started.",
          action: null,
        };
      default:
        return {
          title: "Not found",
          body: "We couldn't find that resource.",
          action: null,
        };
    }
  }

  if (status === 408 || status === 504) {
    switch (op) {
      case "upload":
        return {
          title: "Upload timed out",
          body: "The file may be large. Try again.",
          action: "Try again",
        };
      case "index":
        return {
          title: "Indexing timed out",
          body: "The document is unusually long. Try again.",
          action: "Try again",
        };
      case "ask":
        return {
          title: "Answer timed out",
          body: "The model didn't respond in time. Try again.",
          action: "Try again",
        };
      default:
        return {
          title: "Request timed out",
          body: "Check your connection and try again.",
          action: "Try again",
        };
    }
  }

  if (status === 429) {
    const wait =
      retryAfterSeconds && retryAfterSeconds > 0
        ? `Wait ${retryAfterSeconds}s`
        : "Wait a moment";
    return {
      title: "Too many requests",
      body: `${wait}, then try again.`,
      action: "Try again",
    };
  }

  if (status >= 400 && status < 500) {
    const cleanDetail = detail.trim();
    switch (op) {
      case "upload":
        return {
          title: "We can't accept that file",
          body:
            cleanDetail ||
            "It may be too large, the wrong type, or damaged — try another PDF or DOCX.",
          action: null,
        };
      case "ask":
        return {
          title: "We can't answer that",
          body: cleanDetail || "Try rewording or shortening the question.",
          action: null,
        };
      case "index":
        return {
          title: "Indexing rejected this document",
          body: cleanDetail || "The document may be empty or unreadable.",
          action: null,
        };
      default:
        return {
          title: "We can't process that",
          body: cleanDetail || "Check the input and try again.",
          action: null,
        };
    }
  }

  switch (op) {
    case "upload":
      return {
        title: "Upload failed",
        body: "Something broke on our end. Try again.",
        action: "Try again",
      };
    case "index":
      return {
        title: "Indexing failed",
        body: "Something broke on our end. Try again.",
        action: "Try again",
      };
    case "ask":
      return {
        title: "Couldn't generate an answer",
        body: "Something broke on our end. Try again.",
        action: "Try again",
      };
    case "load":
      return {
        title: "Couldn't load the workspace",
        body: "Something broke on our end. Try again.",
        action: "Try again",
      };
    default:
      return {
        title: "Server error",
        body: "Something broke on our end. Try again.",
        action: "Try again",
      };
  }
}
