"""Application configuration, loaded from environment variables / .env."""

import os

from dotenv import load_dotenv

load_dotenv()

SUPABASE_DB_URL = os.environ.get("SUPABASE_DB_URL")

if not SUPABASE_DB_URL:
    raise RuntimeError("SUPABASE_DB_URL environment variable is not set")

JWT_SECRET_KEY = os.environ.get("JWT_SECRET_KEY")

if not JWT_SECRET_KEY:
    raise RuntimeError("JWT_SECRET_KEY environment variable is not set")

YOUTUBE_API_KEY = os.environ.get("YOUTUBE_API_KEY")

if not YOUTUBE_API_KEY:
    raise RuntimeError("YOUTUBE_API_KEY environment variable is not set")
