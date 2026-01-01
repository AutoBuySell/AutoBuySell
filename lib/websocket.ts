'use client';

type MessageHandler = (data: any) => void;

class WebSocketClient {
  private ws: WebSocket | null = null;
  private url: string;
  private handlers: Set<MessageHandler> = new Set();
  private reconnectInterval: number = 3000;

  constructor(url: string) {
    this.url = url;
  }

  connect() {
    if (this.ws) return;

    this.ws = new WebSocket(this.url);

    this.ws.onopen = () => {
      console.log('WS Connected');
    };

    this.ws.onmessage = (event) => {
      try {
        const message = JSON.parse(event.data);
        this.notify(message);
      } catch (e) {
        console.error('WS Parse Error', e);
      }
    };

    this.ws.onclose = () => {
      console.log('WS Disconnected, reconnecting...');
      this.ws = null;
      setTimeout(() => this.connect(), this.reconnectInterval);
    };

    this.ws.onerror = (err) => {
      console.error('WS Error', err);
      if (this.ws) {
          this.ws.close();
      }
    };
  }

  subscribe(handler: MessageHandler) {
    this.handlers.add(handler);
    return () => this.handlers.delete(handler);
  }

  private notify(data: any) {
    this.handlers.forEach(h => h(data));
  }
}

// Singleton instance
// Note: In Next.js SSR this might be an issue, but we use it in 'use client' components
const WS_URL = 'ws://localhost:8000/api/v1/ws/stream';
export const wsClient = new WebSocketClient(WS_URL);
