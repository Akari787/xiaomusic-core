import type { OnlineSearchPlayReference, PlayRequest } from "./v1Api";

export interface OnlineSearchItemForPlayback {
  title?: string;
  name?: string;
  play_reference?: OnlineSearchPlayReference;
}

export function buildLinkPlayRequest(deviceId: string, url: string, preferProxy: boolean): PlayRequest {
  return {
    device_id: deviceId,
    query: url,
    source_hint: "auto",
    options: { no_cache: false, prefer_proxy: preferProxy },
  };
}

export function buildOnlineSearchPlayRequest(
  deviceId: string,
  item: OnlineSearchItemForPlayback,
): PlayRequest {
  const reference = item.play_reference;
  if (!reference?.query?.trim()) {
    throw new Error("selected search result has no formal playback reference");
  }
  return {
    device_id: deviceId,
    query: reference.query,
    source_hint: reference.source_hint,
    options: { media_id: reference.media_id, title: reference.title },
  };
}
