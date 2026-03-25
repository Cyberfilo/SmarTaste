"""Pydantic models for Apple Music API responses and internal data."""

from __future__ import annotations

from pydantic import BaseModel, Field

# --- Health Check ---


class HealthStatus(BaseModel):
    """Response model for the health check tool."""

    status: str = Field(description="Server status")
    version: str = Field(description="Server version")
    has_developer_token: bool = Field(description="Whether a developer token can be generated")
    has_user_token: bool = Field(description="Whether a Music User Token is configured")
    storefront: str = Field(description="Configured storefront country code")


# --- Apple Music API Response Models ---


class Artwork(BaseModel):
    """Apple Music artwork with URL template."""

    url: str = ""
    width: int | None = None
    height: int | None = None
    bg_color: str | None = Field(default=None, alias="bgColor")

    def url_for_size(self, width: int = 300, height: int = 300) -> str:
        return self.url.replace("{w}", str(width)).replace("{h}", str(height))


class PlayParams(BaseModel):
    id: str = ""
    kind: str = ""


class SongAttributes(BaseModel):
    """Attributes for a song resource."""

    name: str = ""
    artist_name: str = Field(default="", alias="artistName")
    album_name: str = Field(default="", alias="albumName")
    genre_names: list[str] = Field(default_factory=list, alias="genreNames")
    duration_in_millis: int | None = Field(default=None, alias="durationInMillis")
    release_date: str | None = Field(default=None, alias="releaseDate")
    isrc: str | None = None
    artwork: Artwork | None = None
    editorial_notes: dict[str, str] | None = Field(default=None, alias="editorialNotes")
    has_lyrics: bool = Field(default=False, alias="hasLyrics")
    content_rating: str | None = Field(default=None, alias="contentRating")
    audio_traits: list[str] = Field(default_factory=list, alias="audioTraits")
    previews: list[dict] = Field(default_factory=list)
    play_params: PlayParams | None = Field(default=None, alias="playParams")
    url: str | None = None
    disc_number: int | None = Field(default=None, alias="discNumber")
    track_number: int | None = Field(default=None, alias="trackNumber")


class AlbumAttributes(BaseModel):
    """Attributes for an album resource."""

    name: str = ""
    artist_name: str = Field(default="", alias="artistName")
    genre_names: list[str] = Field(default_factory=list, alias="genreNames")
    track_count: int = Field(default=0, alias="trackCount")
    release_date: str | None = Field(default=None, alias="releaseDate")
    artwork: Artwork | None = None
    editorial_notes: dict[str, str] | None = Field(default=None, alias="editorialNotes")
    content_rating: str | None = Field(default=None, alias="contentRating")
    is_single: bool = Field(default=False, alias="isSingle")
    url: str | None = None
    audio_traits: list[str] = Field(default_factory=list, alias="audioTraits")


class ArtistAttributes(BaseModel):
    """Attributes for an artist resource."""

    name: str = ""
    genre_names: list[str] = Field(default_factory=list, alias="genreNames")
    url: str | None = None
    artwork: Artwork | None = None
    editorial_notes: dict[str, str] | None = Field(default=None, alias="editorialNotes")


class PlaylistAttributes(BaseModel):
    """Attributes for a playlist resource."""

    name: str = ""
    description: dict[str, str] | None = None
    artwork: Artwork | None = None
    url: str | None = None
    last_modified_date: str | None = Field(default=None, alias="lastModifiedDate")
    is_chart: bool = Field(default=False, alias="isChart")


class RatingAttributes(BaseModel):
    """Attributes for a rating resource."""

    value: int = 0  # 1 = love, -1 = dislike


class StorefrontAttributes(BaseModel):
    """Attributes for a storefront."""

    name: str = ""
    default_language_tag: str = Field(default="", alias="defaultLanguageTag")


class ActivityAttributes(BaseModel):
    """Attributes for an activity (mood/activity category)."""

    name: str = ""
    url: str | None = None
    artwork: Artwork | None = None
    editorial_notes: dict[str, str] | None = Field(default=None, alias="editorialNotes")


class GenreAttributes(BaseModel):
    """Attributes for a genre resource."""

    name: str = ""
    parent_id: str | None = Field(default=None, alias="parentId")
    parent_name: str | None = Field(default=None, alias="parentName")


class ChartEntry(BaseModel):
    """A single entry in a chart response."""

    chart: str = ""
    name: str = ""
    order_id: str | None = Field(default=None, alias="orderId")
    data: list[Resource] = Field(default_factory=list)


# --- Generic Resource Wrapper ---


class Resource(BaseModel):
    """Generic Apple Music API resource."""

    id: str = ""
    type: str = ""
    href: str | None = None
    attributes: dict = Field(default_factory=dict)
    relationships: dict = Field(default_factory=dict)
    views: dict = Field(default_factory=dict)

    model_config = {"populate_by_name": True}


class PaginatedResponse(BaseModel):
    """Paginated API response with data and optional next URL."""

    data: list[Resource] = Field(default_factory=list)
    next: str | None = None

    @property
    def has_more(self) -> bool:
        return self.next is not None


class SearchResults(BaseModel):
    """Search response with typed result groups."""

    songs: PaginatedResponse = Field(default_factory=PaginatedResponse)
    albums: PaginatedResponse = Field(default_factory=PaginatedResponse)
    artists: PaginatedResponse = Field(default_factory=PaginatedResponse)
    playlists: PaginatedResponse = Field(default_factory=PaginatedResponse)


class ChartResponse(BaseModel):
    """Chart response containing multiple chart types."""

    songs: list[ChartEntry] = Field(default_factory=list)
    albums: list[ChartEntry] = Field(default_factory=list)
    playlists: list[ChartEntry] = Field(default_factory=list)


# Update forward reference
ChartEntry.model_rebuild()
