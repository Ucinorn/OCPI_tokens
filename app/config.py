from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env")

    database_url: str = "postgresql+psycopg://ocpi:ocpi@localhost:5432/ocpi"
    base_url: str = "http://localhost:8000"  # public URL the CPMS reaches us on
    country_code: str = "GB"  # our OCPI party identity (we act as an eMSP)
    party_id: str = "XXX"
    business_name: str = "OCPI Tokens"
    issuer: str = "OCPI Tokens"  # default Token.issuer for new tokens


settings = Settings()
