export function normalizeApiUrlForCache(apiUrl: string): string {
  return apiUrl.replace(/\/+$/g, "");
}
