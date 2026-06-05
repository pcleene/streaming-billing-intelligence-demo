// Tiny EventSource wrapper that survives reconnects + supports typed handlers.

export type SseHandler<T = unknown> = (data: T) => void;

export class SseClient {
  private es: EventSource | null = null;
  private listeners = new Map<string, Set<SseHandler>>();

  constructor(private url = "/api/stream") {}

  start(): void {
    if (this.es) return;
    this.es = new EventSource(this.url);
    this.es.onerror = () => {
      // Browser will auto-reconnect on transient errors.
    };
    for (const [evt, set] of this.listeners) {
      this.bind(evt, set);
    }
  }

  stop(): void {
    this.es?.close();
    this.es = null;
  }

  on<T = unknown>(event: string, handler: SseHandler<T>): () => void {
    let set = this.listeners.get(event);
    if (!set) {
      set = new Set();
      this.listeners.set(event, set);
    }
    set.add(handler as SseHandler);
    if (this.es) this.bind(event, new Set([handler as SseHandler]));
    return () => {
      this.listeners.get(event)?.delete(handler as SseHandler);
    };
  }

  private bind(event: string, handlers: Set<SseHandler>): void {
    if (!this.es) return;
    this.es.addEventListener(event, (e: MessageEvent) => {
      let data: unknown = e.data;
      try {
        data = JSON.parse(e.data);
      } catch {
        /* leave as string */
      }
      for (const h of handlers) h(data);
    });
  }
}
