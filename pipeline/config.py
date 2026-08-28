import os
from dataclasses import dataclass, field
from dotenv import load_dotenv

load_dotenv()

@dataclass(frozen=True)
class Settings:
    project_id: str = os.environ.get("PROJECT_ID", "")
    region: str = os.environ.get("REGION", "us-central1")
    model_id: str = os.environ.get("MODEL_ID", "gemini-3.7-flash")
    bucket: str = os.environ.get("BUCKET", "")
    run_budget_usd: float = float(os.environ.get("RUN_BUDGET_USD", "20.0"))
    critique_qa_rate: float = float(os.environ.get("CRITIQUE_QA_RATE", "0.05"))
    confidence_threshold: float = float(os.environ.get("CONFIDENCE_THRESHOLD", "0.75"))
    lease_seconds: int = int(os.environ.get("LEASE_SECONDS", "600"))
    run_time_cap_seconds: int = int(os.environ.get("RUN_TIME_CAP_SECONDS", "1500"))
    max_attempts: int = int(os.environ.get("MAX_ATTEMPTS", "3"))
    admin_token: str = os.environ.get("ADMIN_TOKEN", "")
    price_in_per_mtok: float = float(os.environ.get("PRICE_IN", "0.75"))
    price_out_per_mtok: float = float(os.environ.get("PRICE_OUT", "3.75"))
    genai_location: str = os.environ.get("GENAI_LOCATION", "global")

settings = Settings()
