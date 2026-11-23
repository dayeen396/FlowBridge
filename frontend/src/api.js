const API_BASE = "/api";

export async function apiRequest(path, options = {}) {
  const headers = new Headers(options.headers ?? {});
  const init = {
    ...options,
    headers,
    credentials: "same-origin",
  };

  if (options.body !== undefined && typeof options.body !== "string") {
    headers.set("Content-Type", "application/json");
    init.body = JSON.stringify(options.body);
  }

  let response;
  try {
    response = await fetch(`${API_BASE}${path}`, init);
  } catch {
    throw new Error("Could not reach the FlowBridge API. Check that the services are running.");
  }

  if (response.status === 204) return null;
  const text = await response.text();
  let payload = null;
  if (text) {
    try {
      payload = JSON.parse(text);
    } catch {
      payload = { error: { message: text } };
    }
  }

  if (!response.ok) {
    const error = new Error(payload?.error?.message ?? `Request failed (${response.status})`);
    error.status = response.status;
    error.code = payload?.error?.code;
    error.issues = payload?.error?.issues ?? [];
    throw error;
  }
  return payload;
}

export function getErrorMessage(error) {
  if (error?.issues?.length) {
    return error.issues.map((issue) => issue.message ?? issue.msg ?? issue.code).join(" · ");
  }
  return error?.message ?? "Something went wrong. Please try again.";
}
