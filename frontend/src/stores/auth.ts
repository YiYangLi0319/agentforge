import { create } from "zustand";

import { api, getToken, setToken } from "../lib/api";

interface AuthState {
  token: string | null;
  username: string | null;
  login: (username: string, password: string) => Promise<void>;
  register: (username: string, password: string, inviteCode?: string) => Promise<void>;
  logout: () => void;
}

interface TokenResponse {
  access_token: string;
  username: string;
}

export const useAuth = create<AuthState>((set) => ({
  token: getToken(),
  username: localStorage.getItem("agentforge_username"),

  login: async (username, password) => {
    const data = await api.post<TokenResponse>("/api/auth/login", { username, password });
    setToken(data.access_token);
    localStorage.setItem("agentforge_username", data.username);
    set({ token: data.access_token, username: data.username });
  },

  register: async (username, password, inviteCode = "") => {
    const data = await api.post<TokenResponse>("/api/auth/register", {
      username,
      password,
      invite_code: inviteCode,
    });
    setToken(data.access_token);
    localStorage.setItem("agentforge_username", data.username);
    set({ token: data.access_token, username: data.username });
  },

  logout: () => {
    setToken(null);
    localStorage.removeItem("agentforge_username");
    set({ token: null, username: null });
  },
}));
