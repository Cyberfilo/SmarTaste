/**
 * TypeScript interfaces for all backend API responses.
 * Matches the backend Pydantic schemas exactly.
 */

// ── Auth ─────────────────────────────────────────────────

export interface User {
  user_id: string;
  email: string;
  display_name: string;
}

export interface AuthResponse {
  user_id: string;
  email: string;
  message: string;
}

// ── Taste Profile ────────────────────────────────────────

export interface GenreEntry {
  genre: string;
  weight: number;
}

export interface ArtistEntry {
  name: string;
  score: number;
  song_count: number;
}

export interface TasteProfile {
  service: string;
  computed_at: string;
  total_songs_analyzed: number;
  listening_hours_estimated: number;
  familiarity_score: number;
  genre_vector: Record<string, number>;
  top_artists: ArtistEntry[];
  audio_trait_preferences: Record<string, number>;
  release_year_distribution: Record<string, number>;
  services_included: string[];
}

export interface TopGenresResponse {
  service: string;
  genres: GenreEntry[];
}

export interface TopArtistsResponse {
  service: string;
  artists: ArtistEntry[];
}

export interface AudioTraitsResponse {
  service: string;
  traits: Record<string, number>;
  note: string | null;
}

// ── Stats ────────────────────────────────────────────────

export interface StatTrackEntry {
  rank: number;
  name: string;
  artist_name: string;
  album_name: string;
  play_count_estimate: number | null;
}

export interface StatArtistEntry {
  rank: number;
  name: string;
  genres: string[];
  score: number | null;
}

export interface StatGenreEntry {
  rank: number;
  genre: string;
  track_count: number;
  artist_count: number;
}

export interface TopTracksResponse {
  service: string;
  period: string;
  items: StatTrackEntry[];
  total: number;
}

export interface TopArtistsStatsResponse {
  service: string;
  period: string;
  items: StatArtistEntry[];
  total: number;
}

export interface TopGenresStatsResponse {
  service: string;
  period: string;
  items: StatGenreEntry[];
  total: number;
}

// ── Recommendations ──────────────────────────────────────

export interface RecommendationItem {
  catalog_id: string;
  name: string;
  artist_name: string;
  album_name: string;
  artwork_url: string;
  preview_url: string;
  score: number;
  explanation: string;
  strategy_source: string;
  genre_names: string[];
}

export interface RecommendationsResponse {
  items: RecommendationItem[];
  strategy: string;
  mood: string | null;
  total: number;
  weights_adapted: boolean;
}

export interface BreakdownDimension {
  name: string;
  label: string;
  score: number;
  weight: number;
}

export interface BreakdownResponse {
  catalog_id: string;
  overall_score: number;
  dimensions: BreakdownDimension[];
  explanation: string;
}

export interface FeedbackResponse {
  catalog_id: string;
  feedback_type: string;
  recorded: boolean;
}

// ── Services ─────────────────────────────────────────────

export interface ServiceConnection {
  service: string;
  status: string;
  service_user_id: string | null;
  connected_at: string | null;
}

export interface ServiceListResponse {
  services: ServiceConnection[];
}

export interface SpotifyConnectResponse {
  authorize_url: string;
}

export interface KeyStatusResponse {
  configured: boolean;
  masked_key: string | null;
  service: string;
}

export interface ValidateKeyResponse {
  valid: boolean;
  error: string | null;
}

export interface CostEstimateResponse {
  model: string;
  estimated_cost_per_message: string;
  input_token_price: string;
  output_token_price: string;
}

// ── Chat ─────────────────────────────────────────────────

export interface MessageItem {
  role: string;
  content: string;
  tool_use: Record<string, unknown> | null;
  tool_result: Record<string, unknown> | null;
}

export interface ConversationListItem {
  id: string;
  title: string;
  message_count: number;
  created_at: string;
  updated_at: string;
}

export interface ConversationResponse {
  id: string;
  title: string;
  messages: MessageItem[];
  created_at: string;
  updated_at: string;
}

export interface ConversationListResponse {
  conversations: ConversationListItem[];
}

// ── Audio Features ───────────────────────────────────────

export interface AudioFeaturesResponse {
  catalog_id: string;
  energy: number | null;
  danceability: number | null;
  valence: number | null;
  acousticness: number | null;
  tempo: number | null;
  instrumentalness: number | null;
  beat_strength: number | null;
  brightness: number | null;
}

// ── Common ───────────────────────────────────────────────

export type Period = "month" | "6months" | "alltime";
