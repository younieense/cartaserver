const KEYS = {
  login: "carta_login",
  password: "carta_password",
  user: "carta_user_json",
};

/** WebSocket URL всегда с того же хоста, что и страница в браузере. */
export function sameOriginWsUrl() {
  const proto = location.protocol === "https:" ? "wss:" : "ws:";
  return `${proto}//${location.host}/ws`;
}

export const session = {
  get serverUrl() {
    return sameOriginWsUrl();
  },
  get login() {
    return localStorage.getItem(KEYS.login) || "";
  },
  set login(v) {
    localStorage.setItem(KEYS.login, v || "");
  },
  get password() {
    return localStorage.getItem(KEYS.password) || "";
  },
  set password(v) {
    localStorage.setItem(KEYS.password, v || "");
  },
  get user() {
    try {
      return JSON.parse(localStorage.getItem(KEYS.user) || "null");
    } catch {
      return null;
    }
  },
  set user(u) {
    if (u) localStorage.setItem(KEYS.user, JSON.stringify(u));
    else localStorage.removeItem(KEYS.user);
  },
  get role() {
    return this.user?.role || "user";
  },
  get isSuperuser() {
    return this.role === "superuser";
  },
  get isAccountant() {
    return this.role === "accountant";
  },
  get canAccessAccounting() {
    return this.isSuperuser || this.isAccountant;
  },
  clearAuth() {
    localStorage.removeItem(KEYS.user);
    localStorage.removeItem(KEYS.password);
  },
};
