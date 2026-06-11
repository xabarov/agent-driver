export function formatStreamError(error: unknown): string {
  const raw = error instanceof Error ? error.message : String(error);
  const lower = raw.toLowerCase();
  if (lower.includes("ssl") || lower.includes("record layer")) {
    return "Connection failed (SSL/network). Retry or check VPN/proxy settings.";
  }
  if (lower.includes("fetch") || lower.includes("network")) {
    return "Network error while streaming. Check backend is running and refresh.";
  }
  if (lower.includes("401") || lower.includes("403")) {
    return "API authentication failed. Check AGENT_DRIVER_API_KEY in .env.";
  }
  return raw || "Stream failed unexpectedly.";
}

export function formatRunFailure(data: Record<string, unknown>): string {
  const reason =
    typeof data.error === "string"
      ? data.error
      : typeof data.message === "string"
        ? data.message
        : typeof data.reason === "string"
          ? data.reason
          : "Run failed";
  const statusCode = typeof data.status_code === "number" ? data.status_code : undefined;
  const lower = `${reason} ${statusCode ?? ""}`.toLowerCase();
  if (statusCode === 402 || lower.includes("402")) {
    return [
      "Provider rejected the request with HTTP 402.",
      reason && reason !== "model_error" ? reason : "Check OpenRouter credits, model availability, or choose another model.",
    ].join(" ");
  }
  if (statusCode === 401 || statusCode === 403 || lower.includes("401") || lower.includes("403")) {
    return "API authentication failed. Check provider API key and model access.";
  }
  if (statusCode === 429 || lower.includes("429")) {
    return "Provider rate limit reached. Retry later or choose another model.";
  }
  return reason || "Run failed.";
}
