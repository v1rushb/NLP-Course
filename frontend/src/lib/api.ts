// API client for PPU backend (FastAPI)
export const API =
  (typeof window !== "undefined" && (window as any).__PPU_API__) ||
  import.meta.env.VITE_PPU_API ||
  "http://localhost:8000";

export type User = {
  id: number;
  email: string;
  name?: string;
  role: "admin" | "student";
  student_id?: string;
};

type AuthResponse = {
  user: User;
  token: string;
};

const TOKEN_KEY = "ppu_token";

export function setApiToken(token: string | null) {
  if (typeof window === "undefined") return;
  if (token) localStorage.setItem(TOKEN_KEY, token);
  else localStorage.removeItem(TOKEN_KEY);
}

function getApiToken() {
  if (typeof window === "undefined") return null;
  return localStorage.getItem(TOKEN_KEY);
}

export type Course = {
  id: number;
  code: string;
  name: string;
  doctor: string;
  days: string;
  time: string;
  capacity: number;
  enrolled_count?: number;
  available_seats?: number;
  grade?: number | null;
};

export type SignupPending = {
  pending: true;
  email: string;
  expires_in: number;
  resend_after: number;
};

export type ChatMsg = {
  id: number;
  user_id: number;
  role: "user" | "assistant";
  text: string;
  ts: number;
};

export type AdminEnrolledCourse = {
  enrollment_id: number;
  course_id: number;
  code: string;
  name: string;
  doctor: string;
  days: string;
  time: string;
  grade: number | null;
};

export type AdminStudent = {
  profile: User;
  enrolled_courses: AdminEnrolledCourse[];
  grades: Array<{ course_id: number; code: string; grade: number | null }>;
  academic_records: {
    enrolled_courses: number;
    graded_courses: number;
    average_grade: number | null;
    standing: string;
  };
};

function errorMessage(data: any, fallback: string) {
  const detail = data?.detail ?? data?.message;
  if (typeof detail === "string") return detail;
  if (detail && typeof detail.message === "string") return detail.message;
  return fallback;
}

async function req<T>(path: string, init?: RequestInit): Promise<T> {
  const headers = new Headers(init?.headers);
  headers.set("Content-Type", "application/json");
  const token = getApiToken();
  if (token) headers.set("Authorization", `Bearer ${token}`);

  const r = await fetch(`${API}${path}`, {
    ...init,
    headers,
  });
  const text = await r.text();
  const data = text ? JSON.parse(text) : null;
  if (!r.ok) throw new Error(errorMessage(data, `Request failed (${r.status})`));
  return data as T;
}

export const api = {
  login: async (email: string, password: string) => {
    const result = await req<AuthResponse>("/login", {
      method: "POST",
      body: JSON.stringify({ email, password }),
    });
    setApiToken(result.token);
    return result.user;
  },
  signup: (body: { email: string; password: string; name: string; student_id?: string }) =>
    req<SignupPending>("/signup", { method: "POST", body: JSON.stringify(body) }),
  verifySignupOtp: async (email: string, otp: string) => {
    const result = await req<AuthResponse>("/signup/verify", {
      method: "POST",
      body: JSON.stringify({ email, otp }),
    });
    setApiToken(result.token);
    return result.user;
  },
  resendSignupOtp: (email: string) =>
    req<SignupPending>("/signup/resend", { method: "POST", body: JSON.stringify({ email }) }),
  courses: () => req<Course[]>("/courses"),
  myCourses: (uid: number) => req<Course[]>(`/my_courses?user_id=${uid}`),
  enroll: (uid: number, courseId: number) =>
    req<{ ok: boolean; course: Course }>("/enrollments", {
      method: "POST",
      body: JSON.stringify({ user_id: uid, course_id: courseId }),
    }),
  drop: (uid: number, courseId: number) =>
    req<{ ok: boolean; course: Course }>("/enrollments", {
      method: "DELETE",
      body: JSON.stringify({ user_id: uid, course_id: courseId }),
    }),
  chat: (uid: number, query: string) =>
    req<{ response: string }>("/chat", {
      method: "POST",
      body: JSON.stringify({ user_id: uid, query }),
    }),
  history: (uid: number) => req<ChatMsg[]>(`/chat/history?user_id=${uid}`),
  clearHistory: (uid: number) =>
    req<{ ok: boolean }>(`/chat/history?user_id=${uid}`, { method: "DELETE" }),
  adminOverview: (adminId: number) =>
    req<any>(`/admin/overview?admin_id=${adminId}`),
  adminStudents: (adminId: number) =>
    req<AdminStudent[]>(`/admin/students?admin_id=${adminId}`),
  updateStudentGrade: (adminId: number, studentId: number, courseId: number, grade: number | null) =>
    req<{ ok: boolean; student: AdminStudent }>(`/admin/students/${studentId}/grades`, {
      method: "PUT",
      body: JSON.stringify({ admin_id: adminId, course_id: courseId, grade }),
    }),
};
