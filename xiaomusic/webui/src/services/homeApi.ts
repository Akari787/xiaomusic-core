import { apiGet, apiPost } from "./apiClient";

// Internal API only: this module wraps non-v1 WebUI helper/tool endpoints.

export async function fetchQrcode<T>(): Promise<T> {
  return (await apiGet<T>("/api/get_qrcode")) as T;
}

export async function cleanTempDir<T>(): Promise<T> {
  return (await apiPost<T>("/api/file/cleantempdir", {})) as T;
}
