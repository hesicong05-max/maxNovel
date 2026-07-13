export type NovelGenre =
  | "玄幻"
  | "都市"
  | "科幻"
  | "武侠"
  | "仙侠"
  | "悬疑"
  | "言情";

export type ProjectStatus =
  | "draft"
  | "worldview_set"
  | "outline_pending"
  | "outline_confirmed"
  | "writing"
  | "completed";

export type StyleIntensity = "mild" | "standard" | "intense";

export interface Project {
  id: string;
  title: string;
  genre: NovelGenre;
  status: ProjectStatus;
  total_chapters: number;
  chapter_word_count: number;
  style_intensity: StyleIntensity;
  created_at: string;
  updated_at: string;
  has_worldview: boolean;
  has_outline: boolean;
  chapter_count: number;
}

export interface Character {
  name: string;
  personality: string;
  background: string;
  motivation: string;
  ability: string;
  relations: { name: string; relation: string }[];
}

export interface Geography {
  name: string;
  description: string;
  significance: string;
}

export interface Faction {
  name: string;
  stance: string;
  power_level: string;
  relations: { name: string; relation: string }[];
}

export interface PowerSystem {
  name: string;
  levels: string;
  rules: string;
  limitations: string;
}

export interface HistoryEvent {
  event: string;
  time: string;
  description: string;
  impact: string;
}

export interface Conflict {
  name: string;
  type: string;
  parties: string;
  stakes: string;
  resolution_hint: string;
}

export interface SpecialSetting {
  name: string;
  description: string;
  rules: string;
}

export interface WorldviewData {
  characters: Character[];
  geography: Geography[];
  factions: Faction[];
  power_system: PowerSystem[];
  history: HistoryEvent[];
  conflicts: Conflict[];
  special_settings: SpecialSetting[];
  raw_text?: string | null;
  source?: WorldviewSource;
}

export type WorldviewSource = "manual" | "imported" | "hybrid";

export interface WorldviewImportResult extends WorldviewData {
  element_count: number;
}

export interface WorldviewElement {
  id: string;
  category: string;
  name: string;
  description: string;
  priority: "core" | "important" | "secondary" | "background";
  revealed: boolean;
  reveal_chapter: number | null;
}

export interface OutlineChapter {
  chapter_num: number;
  title: string;
  summary: string;
  key_events: string[];
  reveal_elements: string[];
}

export interface RevealPlanEntry {
  chapter: number;
  phase: string;
  elements: string[];
  summary: string;
}

export interface OutlineData {
  id: string;
  project_id: string;
  story_arc: string;
  chapters: OutlineChapter[];
  reveal_plan: RevealPlanEntry[];
  created_at?: string;
  updated_at?: string;
}

export interface ChapterData {
  id: string;
  project_id: string;
  chapter_num: number;
  title: string;
  content: string;
  word_count: number;
  summary: string;
  status: string;
  revealed_elements: string[];
}

export interface ChapterListItem {
  id: string;
  chapter_num: number;
  title: string;
  status: string;
  word_count: number;
  revealed_elements: string[];
}

export interface ProgressData {
  total_elements: number;
  revealed_elements: number;
  reveal_percentage: number;
  current_phase: string;
  current_chapter: number;
  total_chapters: number;
  pending_foreshadows: number;
  character_states: Record<string, unknown>;
}

export interface StreamMessage {
  type: "metadata" | "content" | "complete" | "error";
  text?: string;
  chapter_num?: number;
  title?: string;
  elements_to_reveal?: string[];
  phase?: string;
  phase_label?: string;
  word_count?: number;
  target_word_count?: number;
  summary?: string;
  error?: string;
}

// === Word Count Configuration ===

export interface ChapterWordCountInfo {
  chapter_num: number;
  target_word_count: number | null;
  effective_word_count: number;
  title: string;
}

export interface WordCountConfig {
  total_word_count: number | null;
  project_default: number;
  chapters: ChapterWordCountInfo[];
}

// === Batch Generation Stream Messages ===

export interface BatchStreamMessage {
  type:
    | "batch_start"
    | "batch_progress"
    | "metadata"
    | "content"
    | "complete"
    | "error"
    | "batch_complete";
  text?: string;
  chapter_num?: number;
  title?: string;
  elements_to_reveal?: string[];
  phase?: string;
  phase_label?: string;
  target_word_count?: number;
  word_count?: number;
  summary?: string;
  error?: string;
  current?: number;
  total?: number;
  total_chapters?: number;
  chapters_to_generate?: number[];
  total_to_generate?: number;
  total_generated?: number;
  total_words?: number;
  failed_chapters?: number[];
  message?: string;
}

// ═══ Community Module Types ═══

export interface CommunityNovelBrief {
  id: string;
  title: string;
  author_name: string;
  genre: string;
  synopsis: string;
  allow_cocreation: boolean;
  view_count: number;
  like_count: number;
  total_chapters: number;
  total_words: number;
  tags: string[];
  created_at: string;
}

export interface CommunityNovelDetail {
  id: string;
  title: string;
  author_name: string;
  genre: string;
  synopsis: string;
  story_outline: string;
  chapter_notes: string;
  allow_cocreation: boolean;
  view_count: number;
  like_count: number;
  total_chapters: number;
  total_words: number;
  tags: string[];
  project_id: string | null;
  created_at: string;
  updated_at: string;
}

export interface CommunityNovelCreate {
  title: string;
  author_name: string;
  genre: string;
  project_id: string | null;
  synopsis: string;
  story_outline: string;
  chapter_notes: string;
  allow_cocreation: boolean;
  tags: string[];
  total_chapters: number;
  total_words: number;
}

export interface CommunityNovelUpdate {
  title?: string;
  author_name?: string;
  genre?: string;
  synopsis?: string;
  story_outline?: string;
  chapter_notes?: string;
  allow_cocreation?: boolean;
  tags?: string[];
}

export interface CommunityTag {
  id: string;
  name: string;
  usage_count: number;
}

export interface ProjectStats {
  title: string;
  genre: string;
  total_chapters: number;
  chapter_count: number;
  total_words: number;
}

// ═══ Auth Types ═══

export interface AuthUser {
  id: string;
  email: string;
  username: string;
  created_at: string;
}

export interface AuthResponse {
  token: string;
  user: AuthUser;
}

export interface RegisterRequest {
  email: string;
  username: string;
  password: string;
}

export interface LoginRequest {
  email: string;
  password: string;
}
