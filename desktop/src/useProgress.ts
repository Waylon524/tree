import { useEffect, useRef, useState } from "react";
import { wsUrl } from "./api";
import type { Status } from "./types";

// Live progress over the server's WebSocket, with auto-reconnect. The server
// pushes the full status payload whenever it changes (replaces htmx polling).
export function useProgress(token: string): { status: Status | null; connected: boolean } {
  const [status, setStatus] = useState<Status | null>(null);
  const [connected, setConnected] = useState(false);
  const stopped = useRef(false);

  useEffect(() => {
    stopped.current = false;
    let socket: WebSocket | null = null;
    let retry: number | undefined;

    const connect = (): void => {
      socket = new WebSocket(wsUrl());
      socket.onopen = () => setConnected(true);
      socket.onmessage = (event: MessageEvent<string>) => {
        setStatus(JSON.parse(event.data) as Status);
      };
      socket.onclose = () => {
        setConnected(false);
        if (!stopped.current) retry = window.setTimeout(connect, 1500);
      };
      socket.onerror = () => socket?.close();
    };
    connect();

    return () => {
      stopped.current = true;
      if (retry) window.clearTimeout(retry);
      socket?.close();
    };
  }, [token]);

  return { status, connected };
}
