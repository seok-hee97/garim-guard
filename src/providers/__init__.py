"""생성 provider (Gemini 기본, OpenAI 대체)."""

from .base import GenConfig, GenResult, Provider, build_provider

__all__ = ["GenConfig", "GenResult", "Provider", "build_provider"]
