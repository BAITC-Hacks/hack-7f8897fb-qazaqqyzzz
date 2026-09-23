import os
from pathlib import Path
from dataclasses import dataclass

ROOT = Path(__file__).resolve().parent.parent

def load_env():
    path = ROOT / '.env'
    if path.exists():
        for line in path.read_text().splitlines():
            line = line.strip()
            if not line or line.startswith('#') or '=' not in line:
                continue
            name, value = line.split('=', 1)
            os.environ.setdefault(name.strip(), value.strip().strip('\"\''))

load_env()

@dataclass
class Settings:
    root: Path = ROOT
    database: str = os.getenv('DATABASE_PATH', str(ROOT / '.local/careerquest.sqlite3'))
    port: int = int(os.getenv('PORT', '4173'))
    production: bool = os.getenv('APP_ENV', 'development') == 'production'
    origin: str = os.getenv('PUBLIC_ORIGIN', '').rstrip('/')
    api_key: str = os.getenv('OPENAI_API_KEY', '')
    model: str = os.getenv('OPENAI_MODEL', 'gpt-4.1-mini')
    admin_password: str = os.getenv('ADMIN_PASSWORD', '')
    employee_password: str = os.getenv('EMPLOYEE_PASSWORD', '')
    smtp_host: str = os.getenv('SMTP_HOST', '')
    smtp_port: int = int(os.getenv('SMTP_PORT', '587'))
    smtp_username: str = os.getenv('SMTP_USERNAME', '')
    smtp_password: str = os.getenv('SMTP_PASSWORD', '')
    from_email: str = os.getenv('FROM_EMAIL', '')
    smtp_tls: bool = os.getenv('SMTP_TLS', 'true').lower() == 'true'

settings = Settings()
