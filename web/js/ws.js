import { session, sameOriginWsUrl } from "./session.js";

function uid() {
  return crypto.randomUUID?.() || `${Date.now()}-${Math.random().toString(16).slice(2)}`;
}

export class CartaWs {
  constructor() {
    this.ws = null;
    this.connected = false;
    this.authenticated = false;
    this.pending = new Map();
    this.listeners = new Set();
    this.intentionalClose = false;
    this._reconnectTimer = null;
  }

  on(listener) {
    this.listeners.add(listener);
    listener({ type: "connection", connected: this.connected });
    return () => this.listeners.delete(listener);
  }

  _emit(msg) {
    for (const l of this.listeners) {
      try {
        l(msg);
      } catch (_) {}
    }
  }

  connect() {
    this.intentionalClose = false;
    clearTimeout(this._reconnectTimer);
    if (this.ws) {
      try {
        this.ws.close();
      } catch (_) {}
    }
    const url = sameOriginWsUrl();
    const ws = new WebSocket(url);
    this.ws = ws;
    ws.onopen = () => {
      this.connected = true;
      this._emit({ type: "connection", connected: true });
      this._maybeAuth();
    };
    ws.onmessage = (ev) => this._onMessage(ev.data);
    ws.onclose = () => {
      this.connected = false;
      this.authenticated = false;
      this._emit({ type: "connection", connected: false });
      if (!this.intentionalClose) {
        this._reconnectTimer = setTimeout(() => this.connect(), 2500);
      }
    };
    ws.onerror = () => {
      this._emit({ type: "error", message: "Ошибка соединения" });
    };
  }

  disconnect() {
    this.intentionalClose = true;
    clearTimeout(this._reconnectTimer);
    this.ws?.close();
    this.ws = null;
    this.connected = false;
    this.authenticated = false;
  }

  reconnect() {
    this.disconnect();
    this.intentionalClose = false;
    this.connect();
  }

  request(type, payload = {}) {
    return new Promise((resolve, reject) => {
      if (!this.connected || !this.ws || this.ws.readyState !== WebSocket.OPEN) {
        reject(new Error("Нет подключения к серверу"));
        return;
      }
      const id = uid();
      const timer = setTimeout(() => {
        this.pending.delete(id);
        reject(new Error("Таймаут ответа сервера"));
      }, 20000);
      this.pending.set(id, { resolve, reject, timer });
      this.ws.send(JSON.stringify({ id, type, payload }));
    });
  }

  async login(login, password) {
    session.login = login;
    session.password = password;
    if (!this.connected) {
      this.connect();
      await new Promise((r) => setTimeout(r, 700));
    }
    const payload = await this.request("auth", { login, password });
    this.authenticated = true;
    session.user = payload.user;
    return payload;
  }

  async _maybeAuth() {
    if (!session.login || !session.password) return;
    try {
      const payload = await this.request("auth", {
        login: session.login,
        password: session.password,
      });
      this.authenticated = true;
      session.user = payload.user;
      this._emit({ type: "auth_ok", payload });
    } catch (e) {
      this.authenticated = false;
      this._emit({ type: "error", message: e.message || "Ошибка авторизации" });
    }
  }

  _onMessage(text) {
    let json;
    try {
      json = JSON.parse(text);
    } catch {
      this._emit({ type: "error", message: "Некорректный ответ сервера" });
      return;
    }
    const { id, type, payload = {}, event } = json;
    if (id && this.pending.has(id)) {
      const p = this.pending.get(id);
      this.pending.delete(id);
      clearTimeout(p.timer);
      if (type === "error") p.reject(new Error(payload.message || "Ошибка"));
      else p.resolve(payload);
      return;
    }
    if (type === "hello") {
      this.connected = true;
      this._emit({ type: "connection", connected: true });
    }
    if (type === "error") this._emit({ type: "error", message: payload.message || "Ошибка" });
    this._emit({ type: "event", eventType: type, event, payload });
  }
}

export const ws = new CartaWs();
