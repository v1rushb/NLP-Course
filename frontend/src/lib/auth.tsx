import { createContext, useContext, useEffect, useState, type ReactNode } from "react";
import { setApiToken, type User } from "./api";

type AuthCtx = {
  user: User | null;
  setUser: (u: User | null) => void;
  logout: () => void;
};

const Ctx = createContext<AuthCtx>({ user: null, setUser: () => {}, logout: () => {} });

const KEY = "ppu_user";

export function AuthProvider({ children }: { children: ReactNode }) {
  const [user, setUserState] = useState<User | null>(null);

  useEffect(() => {
    try {
      const raw = localStorage.getItem(KEY);
      if (raw) setUserState(JSON.parse(raw));
    } catch {}
  }, []);

  const setUser = (u: User | null) => {
    setUserState(u);
    if (u) localStorage.setItem(KEY, JSON.stringify(u));
    else {
      localStorage.removeItem(KEY);
      setApiToken(null);
    }
  };

  return (
    <Ctx.Provider value={{ user, setUser, logout: () => setUser(null) }}>
      {children}
    </Ctx.Provider>
  );
}

export const useAuth = () => useContext(Ctx);
