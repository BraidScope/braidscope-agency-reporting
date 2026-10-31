# SPDX-License-Identifier: AGPL-3.0-only
from pydantic import BaseModel, Field


class MetricCard(BaseModel):
    label: str
    value: str
    direction: str
    detail: str


class DashboardSummary(BaseModel):
    client_id: str
    days: int
    generated_at: str
    overview: list[MetricCard]
    google_ads: list[MetricCard]
    meta_ads: list[MetricCard]
    shopify: list[MetricCard]
    clicks_by_day: list[int]
    revenue_by_week: list[int]
    top_campaigns: list[dict[str, str]]


class ConnectorField(BaseModel):
    name: str
    label: str
    type: str
    required: bool
    secret: bool = False


class ConnectorContract(BaseModel):
    type: str
    name: str
    mode: str
    auth_type: str
    status: str
    scopes: list[str]
    setup_fields: list[ConnectorField]
    sync_entities: list[str]
    metrics: list[str]
    endpoints: dict[str, str]
    notes: list[str]


class ConnectorSyncRequest(BaseModel):
    client_id: str
    days: int = 30


class ConnectorSyncResponse(BaseModel):
    type: str
    client_id: str
    status: str
    message: str
    synced_entities: list[str]
    generated_at: str
    orders_synced: int = 0
    daily_rows_synced: int = 0
    product_rows_synced: int = 0


class AuthUser(BaseModel):
    sub: str
    email: str | None = None
    role: str | None = None
    access_token: str = Field(exclude=True)
    session_id: str | None = None


class TeamInviteRequest(BaseModel):
    organization_id: str = Field(min_length=1)
    email: str
    role: str = "viewer"


class SessionActivityRequest(BaseModel):
    organization_id: str | None = None
    client_session_id: str | None = None


class ChatRequest(BaseModel):
    question: str
    client_id: str = Field(min_length=1)
    client_name: str | None = None
    connected_sources: list[str] = Field(default_factory=list)
    ai_provider: str | None = None
    ai_model: str | None = None


class OAuthStartRequest(BaseModel):
    client_id: str = Field(min_length=1)
    client_name: str | None = None
    model: str | None = None
    oauth_context: str | None = None


class ClaudeCodeSubmitRequest(BaseModel):
    client_id: str = Field(min_length=1)
    client_name: str | None = None
    code: str = Field(min_length=1, max_length=1000)


class BYOKSaveRequest(BaseModel):
    client_id: str = Field(min_length=1)
    client_name: str | None = None
    provider: str
    api_key: str = Field(min_length=1)
    model: str | None = None


class AIModelPreviewRequest(BaseModel):
    client_id: str = Field(min_length=1)
    client_name: str | None = None
    provider: str
    api_key: str | None = None


class AIDisconnectRequest(BaseModel):
    client_id: str = Field(min_length=1)
    client_name: str | None = None
    provider: str


class DataDisconnectRequest(BaseModel):
    client_id: str = Field(min_length=1)
    client_name: str | None = None
    source_type: str


class ShopifyOAuthStartResponse(BaseModel):
    auth_url: str
    state_expires_at: str
