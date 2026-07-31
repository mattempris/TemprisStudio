from functools import lru_cache
from pathlib import Path

from pydantic_settings import BaseSettings, SettingsConfigDict

BACKEND_DIR = Path(__file__).resolve().parents[2]
DATA_DIR = BACKEND_DIR / "data"
MODELS_DIR = DATA_DIR / "models"
DEFAULTS_DIR = DATA_DIR / "defaults"

# The three fine-tuned Qwen embedding models live one level up in the monorepo,
# zipped, until `scripts/prepare_embedding_models.py` unzips them into MODELS_DIR.
SOURCE_MODELS_DIR = BACKEND_DIR.parents[1] / "models"


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    # Anthropic
    anthropic_api_key: str
    anthropic_model: str = "claude-sonnet-5"

    # Azure Blob Storage (temprisdev, per instructions.txt)
    azure_tenant_id: str
    azure_client_id: str
    azure_client_secret: str
    azure_blob_account: str = "temprisdev"
    azure_blob_container: str = "jastudio"

    # Application
    app_host: str = "0.0.0.0"
    app_port: int = 9400
    cors_origins: str = "http://localhost:5173,http://localhost:4673"
    max_file_size_mb: int = 50

    # Embeddings
    embedding_device: str = "cuda"  # falls back to cpu automatically if unavailable

    # Clustering / stability gating defaults (see plan's methodology section)
    stability_n_perturb: int = 50
    stability_subsample_frac: float = 0.9
    stability_gate: float = 0.58
    self_consistency_conf_threshold: float = 0.45
    self_consistency_votes: int = 3
    catch_all_reviewers: int = 5

    @property
    def cors_origins_list(self) -> list[str]:
        return [o.strip() for o in self.cors_origins.split(",") if o.strip()]


@lru_cache
def get_settings() -> Settings:
    return Settings()
