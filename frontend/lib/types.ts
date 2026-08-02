export type Corner = "behavior" | "health" | "fun-facts" | "special-moments";
export type AppScreen = "welcome" | "profile" | "hub" | Corner;
export type BrandAccent = "#E43D12" | "#D6536D" | "#EFB11D" | "#FFA2B6";
export type CatSex = "male" | "female" | "unknown";

/**
 * A usable session. Every authenticated call takes one of these, so the token
 * fields are non-nullable by construction.
 */
export type AuthSession = {
  status: "active";
  access_token: string;
  refresh_token: string;
  expires_in_seconds: number;
};

/**
 * Sign-up succeeded but Supabase issued no session: the account exists and is
 * unusable until the emailed confirmation link is followed. Production has
 * email confirmation enabled, so this is the normal sign-up outcome.
 */
export type PendingConfirmation = {
  status: "confirmation_required";
  access_token: null;
  refresh_token: null;
  expires_in_seconds: null;
};

/** Discriminate on `status` before reading any token. */
export type AuthResult = AuthSession | PendingConfirmation;

export type CatAge = {
  value: number;
  unit: "months" | "years";
};

export type CatWeight = {
  value: number;
  unit: "kg" | "lb";
};

export type CatTheme = {
  primary_color: string;
  accent_color: string;
};

export type CatProfile = {
  id: string;
  account_id: string;
  name: string;
  age: CatAge;
  breed: string | null;
  sex: CatSex;
  weight: CatWeight;
  energy_level: 1 | 2 | 3 | 4 | 5;
  common_patterns: string;
  known_conditions: string[];
  photo_references: string[];
  theme: CatTheme;
  created_at: string;
  updated_at: string;
};

export type CatCreateInput = Omit<
  CatProfile,
  "id" | "account_id" | "created_at" | "updated_at"
> & {
  cat_id: string;
};

export type BehaviorInterpretation = {
  interpretation: string;
  answer_mode: "corpus_grounded" | "general_knowledge";
  confidence: "well-established" | "general" | "varies-by-cat";
  reasoning: string;
  cited_entry_ids: string[];
  retrieved_entry_ids: string[];
  cited_entries: BehaviorCitation[];
  suggested_clarifying_questions: string[];
  medical_nudge: boolean;
};

export type BehaviorCitation = {
  entry_id: string;
  title: string;
  organization: string;
  url: string | null;
};

export type BehaviorChatResponse = {
  session_id: string;
  generation_id: string;
  result: BehaviorInterpretation;
};

export type BodySystem =
  | "dental"
  | "digestive"
  | "ears"
  | "eyes"
  | "kidney"
  | "musculoskeletal"
  | "neurological"
  | "respiratory"
  | "skin"
  | "systemic"
  | "toxin"
  | "urinary";

export type SymptomIntake = {
  body_systems: BodySystem[];
  duration_hours: number | null;
  appetite_change:
    | "unknown"
    | "no-change"
    | "decreased"
    | "increased"
    | "not-eating";
  vomiting: "unknown" | "none" | "once" | "repeated";
  litter_box_change: boolean | null;
  breathing_change: boolean | null;
  lethargy: boolean | null;
  free_text_residual: string;
};

export type Claim = {
  text: string;
  source_entry_id: string;
  source_title: string | null;
  source_organization: string | null;
  source_url: string | null;
};

export type TriageResult = {
  severity: "emergency" | "urgent" | "monitor" | "routine";
  claims: Claim[];
  message: string;
  retrieved_entry_ids: string[];
  response_kind:
    | "triage"
    | "emergency_canned"
    | "no_reliable_information";
};

export type HealthChatResponse = {
  session_id: string;
  generation_id: string;
  result: TriageResult;
};

export type FunFact = {
  id: string;
  fact: string;
  category:
    | "age"
    | "behavior"
    | "breed"
    | "coat"
    | "cognition"
    | "communication"
    | "history"
    | "senses";
  tags: string[];
  tone: "playful" | "informative";
  personalization_hook: string;
  source_note: string;
  source_url: string | null;
};

export type FunFactDetail = FunFact & {
  detail: string;
};

export type MomentKind = "photo" | "video" | "note" | "date";

export type Moment = {
  id: string;
  cat_id: string;
  kind: MomentKind;
  title: string;
  body: string | null;
  media_key: string | null;
  event_date: string | null;
  created_at: string;
};

export type ApiErrorBody = {
  code?: string;
  message?: string;
  retryable?: boolean;
  detail?: unknown;
};

export type BehaviorMessage =
  | { id: string; role: "user"; text: string }
  | {
      id: string;
      role: "assistant";
      text: string;
      generation_id: string;
      result: BehaviorInterpretation;
    };

export type HealthExchange = {
  id: string;
  concern: string;
  generation_id: string;
  result: TriageResult;
};
