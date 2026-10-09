"""Contrat d'entrée / sortie de l'API (validé par Pydantic, publié dans OpenAPI).

TP1, partie 2 : complétez les schémas. Mode : SANS IA pour cette partie.
"""

from __future__ import annotations

from datetime import UTC, datetime

from pydantic import AwareDatetime, BaseModel, ConfigDict, Field, field_validator, model_validator


class PredictionRequest(BaseModel):
    """Une observation de station à l'instant t."""

    model_config = ConfigDict(extra="forbid")

    station_id: int = Field(..., ge=1, description="Identifiant de la station")
    timestamp: AwareDatetime = Field(..., description="Instant de l'observation, avec fuseau horaire")
    capacity: int = Field(..., gt=0, le=100, description="Nombre de bornes de la station")
    bikes_available: int = Field(..., ge=0, description="Vélos disponibles à l'instant t")
    temperature: float = Field(..., ge=-30, le=50, description="Température en °C")
    is_raining: bool = Field(..., description="Il pleut à l'instant t")

    @field_validator("timestamp")
    @classmethod
    def _to_utc(cls, value: datetime) -> datetime:
        return value.astimezone(UTC)

    @model_validator(mode="after")
    def _bikes_within_capacity(self) -> PredictionRequest:
        if self.bikes_available > self.capacity:
            raise ValueError("bikes_available ne peut pas dépasser capacity")
        return self


class PredictionResponse(BaseModel):
    station_id: int
    target_timestamp: AwareDatetime = Field(..., description="Instant prédit (t + 1 h)")
    predicted_bikes: float = Field(..., ge=0)
    model_version: str


# STRETCH : BatchPredictionRequest (1 à 1000 PredictionRequest) et BatchPredictionResponse
