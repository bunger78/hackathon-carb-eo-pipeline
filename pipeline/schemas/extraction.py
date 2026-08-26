from typing import Literal, Optional
from pydantic import BaseModel, Field

Category = Literal["intake", "boost", "cat", "engine", "exhaust", "ignition", "tune", "other"]

class FitmentRow(BaseModel):
    part_numbers: list[str] = Field(default_factory=list, description="Part numbers listed FOR THIS ROW; empty if the document does not associate PNs per row")
    year_start: Optional[int] = None
    year_end: Optional[int] = None
    make: Optional[str] = None
    model: Optional[str] = None
    trim_note: Optional[str] = None
    displacement_l: Optional[float] = None
    induction: Optional[Literal["NA", "TURBO", "SC"]] = None
    cylinders: Optional[int] = None

class Extraction(BaseModel):
    eo_number: str
    issue_date: Optional[str] = Field(None, description="YYYY-MM-DD")
    manufacturer: Optional[str] = None
    device_name: Optional[str] = None
    category: Optional[Category] = None
    description: Optional[str] = None
    supersedes: list[str] = Field(default_factory=list, description="EO numbers this order supersedes/cancels")
    part_numbers: list[str] = Field(default_factory=list, description="ALL part numbers in the document")
    fitment: list[FitmentRow] = Field(default_factory=list)
    confidence: float = Field(ge=0, le=1)
    sections_confidence: dict[str, float] = Field(default_factory=dict)
    illegible_pages: list[int] = Field(default_factory=list)
    notes: Optional[str] = None

class CritiqueVerdict(BaseModel):
    verdict: Literal["accept", "fix", "escalate"]
    corrections: Optional[dict] = None
    reasons: list[str] = Field(default_factory=list, description="Each reason cites a page number")

class ResolverDecision(BaseModel):
    fitment_index: int
    vehicle_ids: list[str]
    rationale: str = Field(description="One line explaining the decision")
    confidence: float = Field(ge=0, le=1)

class ResolverBatch(BaseModel):
    decisions: list[ResolverDecision]
