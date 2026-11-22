import os
from pathlib import Path


def _load_dotenv() -> None:
    for cand in (Path(".env"), Path(__file__).resolve().parent.parent / ".env"):
        if cand.exists():
            for line in cand.read_text(encoding="utf-8").splitlines():
                line = line.strip()
                if not line or line.startswith("#") or "=" not in line:
                    continue
                k, v = line.split("=", 1)
                os.environ.setdefault(k.strip(), v.strip().strip('"').strip("'"))
            break


_load_dotenv()


def env(name: str, default: str = "") -> str:
    return os.environ.get(name, default)


ROOT = Path(__file__).resolve().parent.parent


class Settings:
    database_url = env("DATABASE_URL", f"sqlite:///{ROOT}/storage/app.db")
    storage_backend = env("STORAGE_BACKEND", "local")
    storage_dir = Path(env("STORAGE_DIR", str(ROOT / "storage")))
    s3_endpoint = env("S3_ENDPOINT", "https://storage.yandexcloud.net")
    s3_bucket = env("S3_BUCKET")
    s3_access_key = env("S3_ACCESS_KEY")
    s3_secret_key = env("S3_SECRET_KEY")
    s3_region = env("S3_REGION", "ru-central1")
    yc_folder_id = env("YC_FOLDER_ID")
    yc_api_key = env("YC_API_KEY")
    llm_model_extract = env("LLM_MODEL_EXTRACT", "yandexgpt/latest")
    llm_model_cheap = env("LLM_MODEL_CHEAP", "yandexgpt-lite/latest")
    ocr_enabled = env("OCR_ENABLED", "1") == "1"
    library_dir = Path(env("LIBRARY_DIR", str(ROOT / "Летопись природы")))
    workers = int(env("WORKERS", "4"))
    max_upload_mb = int(env("MAX_UPLOAD_MB", "300"))
    llm_enabled = env("LLM_ENABLED", "1") == "1"
    vision_model = env("VISION_MODEL", "qwen3.6-35b-a3b/latest")
    vision_enabled = env("VISION_ENABLED", "1") == "1"
    scan_dpi = int(env("SCAN_DPI", "200"))
    verify_enabled = env("VERIFY_ENABLED", "1") == "1"
    force_ocr = env("FORCE_OCR", "0") == "1"
    vision_max_pages = int(env("VISION_MAX_PAGES", "300"))

    @property
    def yc_ready(self) -> bool:
        return bool(self.yc_folder_id and self.yc_api_key)


settings = Settings()
