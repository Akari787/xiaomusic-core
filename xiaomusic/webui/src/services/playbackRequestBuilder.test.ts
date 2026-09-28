import { describe, expect, it } from "vitest";
import fixtures from "./playback_contract_fixtures.json";
import { buildLinkPlayRequest, buildOnlineSearchPlayRequest } from "./playbackRequestBuilder";
import type { OnlineSearchPlayReference } from "./v1Api";

describe("playback request builder", () => {
  it("builds a normal link payload without phantom options", () => {
    expect(buildLinkPlayRequest("did-fixture", "https://example.com/audio.mp3", false)).toEqual(
      fixtures.link,
    );
  });

  it("builds a prefer-proxy payload using only declared options", () => {
    expect(buildLinkPlayRequest("did-fixture", "https://example.com/audio.mp3", true)).toEqual(
      fixtures.proxy_link,
    );
  });

  it("requires a formal search play reference and never falls back to title", () => {
    expect(() => buildOnlineSearchPlayRequest("did-1", { title: "Title only" })).toThrow(
      "no formal playback reference",
    );
    const reference: OnlineSearchPlayReference = {
      query: fixtures.online_plugin.query,
      source_hint: "online_plugin",
      media_id: fixtures.online_plugin.options.media_id,
      title: fixtures.online_plugin.options.title,
    };
    expect(buildOnlineSearchPlayRequest("did-fixture", { title: "Fixture Song", play_reference: reference })).toEqual(
      fixtures.online_plugin,
    );
  });
});
