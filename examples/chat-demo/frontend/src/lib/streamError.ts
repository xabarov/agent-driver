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
