"""Central configuration for VeriNexus.

All tunable constants live here so the algorithm has exactly one source of truth.
The CEVS weights and thresholds mirror docs/ALGORITHM.md sections 4-6; changing a
value here changes the documented behaviour, so update the doc alongside it.
"""
import os
from typing import List

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=".env", env_file_encoding="utf-8", case_sensitive=False, extra="ignore"
    )

    # ---------------------------------------------------------------- LLM
    GROQ_API_KEY: str = ""
    GROQ_MODEL: str = "llama-3.3-70b-versatile"
    # Used on the final retry when the primary model is rate-limited or erroring.
    GROQ_FAST_MODEL: str = "llama-3.1-8b-instant"

    # ---------------------------------------------------------------- web evidence
    TAVILY_API_KEY: str = ""
    RESULTS_PER_QUERY: int = 5

    # ---------------------------------------------------------------- storage
    MONGODB_URI: str = ""
    MONGODB_DB: str = "verinexus"
    CHROMA_DIR: str = "./data/chroma"
    UPLOAD_DIR: str = "./data/uploads"

    # ---------------------------------------------------------------- models
    EMBED_MODEL: str = "all-MiniLM-L6-v2"
    # "local" runs a DeBERTa NLI head in-process; "llm" asks Groq to judge instead.
    NLI_BACKEND: str = "llm"
    LOCAL_NLI_MODEL: str = "MoritzLaurer/DeBERTa-v3-base-mnli"

    # ---------------------------------------------------------------- ingestion
    CHUNK_WORDS: int = 220
    CHUNK_OVERLAP: int = 40

    # ---------------------------------------------------------------- retrieval
    RRF_K: int = 60                    # ALGORITHM.md section 2
    EVIDENCE_PER_CLAIM: int = 5        # k in hybrid_retrieve(c_j, E, k=5)
    DRAFT_CONTEXT_CHUNKS: int = 12

    # ---------------------------------------------------------------- planning
    MAX_SUB_QUESTIONS: int = 5
    MAX_REPAIR_ROUNDS: int = 2         # loop bound R; worst case 3 draft passes

    # ---------------------------------------------------------------- CEVS weights
    # w_s + w_a + w_q = 0.85, deliberately not 1.0: one perfect uncorroborated
    # source must not reach certainty. w_x > w_a: a credible contradiction
    # outweighs a second agreeing source.
    W_SUPPORT: float = 0.45
    W_AGREEMENT: float = 0.25
    W_AUTHORITY: float = 0.15
    W_CONTRADICTION: float = 0.35

    # ---------------------------------------------------------------- thresholds
    ENTAIL_THRESHOLD: float = 0.60     # theta_e
    REVIEW_THRESHOLD: float = 0.55     # tau_r, below this the run goes to a human

    BAND_VERIFIED: float = 0.75
    BAND_PARTIAL: float = 0.55
    BAND_WEAK: float = 0.30

    CONFLICT_CONTRA_MIN: float = 0.50
    CONFLICT_SUPPORT_MIN: float = 0.50

    PENALTY_CONFLICT: float = 0.15
    PENALTY_SINGLE_SOURCE: float = 0.20

    # ---------------------------------------------------------------- server
    CORS_ORIGINS: List[str] = [
        "http://localhost:5173",
        "http://127.0.0.1:5173",
        "http://localhost:3000",
    ]

    def validate(self) -> List[str]:
        """Return the names of config values needed for a full run but not set.

        Truthy result means the server is running degraded, not that it is broken:
        /api/health reports it and /api/research refuses only when GROQ_API_KEY
        is the missing piece.
        """
        missing: List[str] = []
        if not self.GROQ_API_KEY:
            missing.append("GROQ_API_KEY")
        if not self.TAVILY_API_KEY:
            missing.append("TAVILY_API_KEY")
        if not self.MONGODB_URI:
            missing.append("MONGODB_URI")
        return missing

    def ensure_dirs(self) -> None:
        for path in (self.CHROMA_DIR, self.UPLOAD_DIR):
            os.makedirs(path, exist_ok=True)


settings = Settings()
settings.ensure_dirs()
