"""Database configuration."""

import os
from dotenv import load_dotenv

load_dotenv()


class DatabaseConfig:
    """Database configuration settings."""
    
    # Connection details
    HOST = "ai-financial-assistant-bollette.e.aivencloud.com"
    PORT = 22782
    DATABASE = "defaultdb"
    USER = "avnadmin"
    PASSWORD = os.getenv("DB_PASSWORD", "")
    SSL_MODE = "require"
    
    @classmethod
    def get_database_url(cls) -> str:
        """Get the database URL for SQLAlchemy.
        
        Returns:
            Database connection URL
        """
        return (
            f"postgresql://{cls.USER}:{cls.PASSWORD}@"
            f"{cls.HOST}:{cls.PORT}/{cls.DATABASE}?sslmode={cls.SSL_MODE}"
        )
    
    @classmethod
    def is_development(cls) -> bool:
        """Check if the application is running in development mode.
        
        Returns:
            True if in development mode, False otherwise
        """
        env = os.getenv("ENVIRONMENT", "development").lower()
        return env == "development"
