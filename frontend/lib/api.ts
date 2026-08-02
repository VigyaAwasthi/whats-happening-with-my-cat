import type {
  ApiErrorBody,
  AuthResult,
  BehaviorChatResponse,
  CatCreateInput,
  CatProfile,
  Corner,
  FunFact,
  FunFactDetail,
  HealthChatResponse,
  Moment,
  MomentKind,
  SymptomIntake,
} from "@/lib/types";

export const API_BASE_URL =
  process.env.NEXT_PUBLIC_API_BASE_URL ?? "http://127.0.0.1:8000";
export const NO_ACTIVE_CAT_EVENT = "whisker-rooms:no-active-cat";

export class ApiError extends Error {
  code: string;
  status: number;
  retryable: boolean;

  constructor(status: number, body: ApiErrorBody) {
    super(body.message ?? "The request could not be completed.");
    this.name = "ApiError";
    this.code = body.code ?? "UNKNOWN";
    this.status = status;
    this.retryable = body.retryable ?? false;
  }
}

type RequestOptions = Omit<RequestInit, "body"> & {
  token?: string | null;
  catId?: string;
  body?: unknown;
};

async function request<T>(path: string, options: RequestOptions = {}): Promise<T> {
  const { token, catId, body, headers, ...init } = options;
  const response = await fetch(`${API_BASE_URL}${path}`, {
    ...init,
    headers: {
      ...(body !== undefined ? { "Content-Type": "application/json" } : {}),
      ...(token ? { Authorization: `Bearer ${token}` } : {}),
      ...(catId ? { "X-Active-Cat-ID": catId } : {}),
      ...headers,
    },
    body: body === undefined ? undefined : JSON.stringify(body),
  });

  if (!response.ok) {
    let error: ApiErrorBody = {};
    try {
      error = (await response.json()) as ApiErrorBody;
    } catch {
      error = { message: `Request failed with status ${response.status}.` };
    }
    const apiError = new ApiError(response.status, error);
    if (
      apiError.code === "NO_ACTIVE_CAT" &&
      typeof window !== "undefined"
    ) {
      window.dispatchEvent(new Event(NO_ACTIVE_CAT_EVENT));
    }
    throw apiError;
  }

  if (response.status === 204) {
    return undefined as T;
  }
  return (await response.json()) as T;
}

export const catApi = {
  // Returns `status: "confirmation_required"` when Supabase email confirmation
  // is on — the account exists but there is no session to use yet.
  signUp(email: string, password: string) {
    return request<AuthResult>("/auth/sign-up", {
      method: "POST",
      body: { email, password },
    });
  },
  signIn(email: string, password: string) {
    return request<AuthResult>("/auth/sign-in", {
      method: "POST",
      body: { email, password },
    });
  },
  async listCats(token: string) {
    const response = await request<{ cats: CatProfile[] }>("/cats", { token });
    return response.cats;
  },
  async createCat(token: string, input: CatCreateInput) {
    const response = await request<{ cat: CatProfile }>("/cats", {
      method: "POST",
      token,
      body: input,
    });
    return response.cat;
  },
  async patchCat(
    token: string,
    input: { cat_id: string } & Partial<Omit<CatCreateInput, "cat_id" | "id">>,
  ) {
    const response = await request<{ cat: CatProfile }>("/cats", {
      method: "PATCH",
      token,
      body: input,
    });
    return response.cat;
  },
  deleteCat(token: string, catId: string) {
    const params = new URLSearchParams({ cat_id: catId });
    return request<{ deleted_id: string; deleted: boolean }>(
      `/cats?${params.toString()}`,
      { method: "DELETE", token },
    );
  },
  behavior(
    token: string,
    catId: string,
    message: string,
    sessionId: string,
  ) {
    return request<BehaviorChatResponse>("/chat/behavior", {
      method: "POST",
      token,
      catId,
      body: { cat_id: catId, message, session_id: sessionId },
    });
  },
  health(
    token: string,
    catId: string,
    sessionId: string,
    message: string | null,
    intake: SymptomIntake | null,
  ) {
    return request<HealthChatResponse>("/chat/health", {
      method: "POST",
      token,
      catId,
      body: { cat_id: catId, message, intake, session_id: sessionId },
    });
  },
  async facts(
    token: string,
    catId: string,
    tags: string[],
    excludeIds: string[] = [],
  ) {
    const params = new URLSearchParams({ cat_id: catId });
    tags.forEach((tag) => params.append("tags", tag));
    excludeIds.forEach((id) => params.append("exclude_ids", id));
    const response = await request<{ facts: FunFact[] }>(
      `/facts?${params.toString()}`,
      { token, catId },
    );
    return response.facts;
  },
  getFact(token: string, catId: string, factId: string) {
    const params = new URLSearchParams({ cat_id: catId });
    return request<FunFactDetail>(
      `/facts/${encodeURIComponent(factId)}?${params.toString()}`,
      { token, catId },
    );
  },
  async moments(token: string, catId: string) {
    const params = new URLSearchParams({ cat_id: catId });
    const response = await request<{ cat_id: string; moments: Moment[] }>(
      `/moments?${params.toString()}`,
      { token, catId },
    );
    return response.moments;
  },
  async createMoment(
    token: string,
    input: {
      cat_id: string;
      kind: MomentKind;
      title: string;
      body?: string | null;
      media_key?: string | null;
      event_date?: string | null;
    },
  ) {
    const response = await request<{ moment: Moment }>("/moments", {
      method: "POST",
      token,
      catId: input.cat_id,
      body: input,
    });
    return response.moment;
  },
  deleteMoment(token: string, catId: string, momentId: string) {
    const params = new URLSearchParams({
      cat_id: catId,
      moment_id: momentId,
    });
    return request<{ deleted_id: string; deleted: boolean }>(
      `/moments?${params.toString()}`,
      { method: "DELETE", token, catId },
    );
  },
  feedback(
    token: string,
    input: {
      cat_id: string;
      session_id: string;
      corner: Corner;
      thumb: "up" | "down";
      generation_id: string;
      helpfulness_score?: number | null;
    },
  ) {
    return request("/feedback", { method: "POST", token, body: input });
  },
  exportAccount(token: string) {
    return request<unknown>("/account/export", { token });
  },
  deleteAccount(token: string) {
    return request<{ account_id: string; deleted: boolean }>("/account", {
      method: "DELETE",
      token,
    });
  },
};
