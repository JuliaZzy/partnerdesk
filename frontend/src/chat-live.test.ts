import { beforeEach, describe, expect, it } from "vitest";

import { chatPresence, flightOf, forgetChat, isChatBusy, isForgotten, patchFlight, resetChatLive, setActiveChat } from "./chat-live";

describe("per-chat live state", () => {
  beforeEach(() => resetChatLive());

  it("keeps a reply and a draft on the chat that owns them", () => {
    patchFlight("a", { busy: true, live: "one", draft: "still typing" });
    patchFlight("b", { draft: "other" });
    expect(flightOf("a")).toMatchObject({ busy: true, live: "one", draft: "still typing" });
    expect(flightOf("b")).toMatchObject({ busy: false, live: "", draft: "other" });
    expect(isChatBusy("a")).toBe(true);
    expect(isChatBusy("b")).toBe(false);
    expect(chatPresence().busyIds.slice().sort()).toEqual(["a"]);
  });

  it("a token on one chat does not replace the other's flight or the sidebar snapshot", () => {
    setActiveChat("a");
    patchFlight("a", { busy: true, live: "one" });
    const flight = flightOf("a");
    const sidebar = chatPresence();
    patchFlight("a", { live: "one more" });
    expect(flightOf("a")).not.toBe(flight);
    expect(flightOf("b")).toMatchObject({ busy: false, live: "" });
    expect(chatPresence()).toBe(sidebar);
    expect(chatPresence().activeId).toBe("a");
  });

  it("a deleted chat ignores a reply that arrives late", () => {
    setActiveChat("a");
    patchFlight("a", { busy: true, live: "one" });
    patchFlight("b", { live: "keep" });
    forgetChat("a");
    patchFlight("a", { live: "late", busy: true });
    expect(isForgotten("a")).toBe(true);
    expect(isChatBusy("a")).toBe(false);
    expect(flightOf("a").live).toBe("");
    expect(flightOf("b").live).toBe("keep");
    expect(chatPresence().activeId).toBeNull();
    expect(chatPresence().busyIds).toEqual([]);
  });
});
