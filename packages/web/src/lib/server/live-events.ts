import { EventEmitter } from "node:events";

/** Revision-change event emitted when a project snapshot swaps to a new revision. */
export type LiveRevisionEvent = {
  project: string;
  revision: string;
};

type LiveEventGlobals = {
  envoiLiveEventBus: EventEmitter;
};

const liveEventGlobals = globalThis as unknown as Partial<LiveEventGlobals>;
const liveEventBus = (liveEventGlobals.envoiLiveEventBus ??=
  new EventEmitter());

/** Publish a revision-change event for a project snapshot. */
export function publishLiveRevisionEvent(event: LiveRevisionEvent): void {
  liveEventBus.emit("revision", event);
}

/** Subscribe to revision-change events and return an unsubscribe function. */
export function subscribeLiveRevisionEvent(
  listener: (event: LiveRevisionEvent) => void,
): () => void {
  liveEventBus.on("revision", listener);
  return () => {
    liveEventBus.off("revision", listener);
  };
}
