export async function api<T = unknown>(path: string, init?: RequestInit): Promise<T> {
  const res = await fetch(path, {
    headers: { "Content-Type": "application/json" },
    ...init,
  });
  if (res.status === 401) {
    window.dispatchEvent(new Event("kuro:unauthorized"));
    throw new Error("未登录");
  }
  if (!res.ok) {
    const body = await res.json().catch(() => ({}));
    throw new Error((body as { detail?: string }).detail ?? `请求失败（${res.status}）`);
  }
  return res.json() as Promise<T>;
}

export interface Overview {
  students_total: number;
  wrong_open: number;
  wrong_mastered: number;
  pending_tasks: number;
  checkin_today: number;
  by_student: {
    id: number;
    nickname: string;
    stage: string;
    avg_mastery: number | null;
    due_count: number | null;
  }[];
}

export interface StudentDetail {
  id: number;
  nickname: string;
  stage: string;
  note: string;
  mastery: { name: string; subject: string; mastery: number; confidence: number }[];
  wrongs: {
    id: number;
    subject: string;
    question: string;
    error_type: string;
    status: string;
    times_wrong: number;
  }[];
  courses: { id: number; title: string; start: string; status: string; classroom_url: string }[];
  effect: {
    review_pass_rate: number | null;
    reviewed_count: number;
    wrong_total: number;
    wrong_mastered: number;
    due_count: number;
    mastery_now: number | null;
    mastery_delta: number | null;
    trend: { date: string; avg_mastery: number; due_count: number }[];
  };
}

export interface ScheduleItem {
  id: number;
  student: string;
  kind: string;
  fire_at: string;
}
