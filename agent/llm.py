"""
agent/llm.py — Centralized LLM Factory Configuration

Supports multiple model providers including free local/cloud options:
- OpenAI (GPT-4o)
- Anthropic (Claude 3.5 Sonnet)
- Groq (Free fast inference - Llama 3 API)
- Ollama (Completely free, runs locally) 
"""

from langchain_openai import ChatOpenAI
from langchain_anthropic import ChatAnthropic
from langchain_groq import ChatGroq
from langchain_community.chat_models import ChatOllama

from backend.config import settings

def get_llm():
    """Returns the configured LangChain chat model based on the .env settings."""
    model_name = settings.MODEL.lower()

    # 1. Anthropic / Claude
    if model_name.startswith("claude"):
        return ChatAnthropic(
            model=settings.MODEL,
            api_key=settings.ANTHROPIC_API_KEY,
            temperature=0,
        )
    
    # 2. Groq (Free API - very fast)
    elif "llama" in model_name and settings.GROQ_API_KEY:
        return ChatGroq(
            model=settings.MODEL,
            api_key=settings.GROQ_API_KEY,
            temperature=0,
        )
        
    # 3. Ollama (Free Local Inference)
    elif settings.OLLAMA_BASE_URL and ("ollama" in model_name or settings.OPENAI_API_KEY == ""):
        # If MODEL="llama3" and we are using Ollama
        clean_model_name = settings.MODEL.replace("ollama/", "")
        return ChatOllama(
            base_url=settings.OLLAMA_BASE_URL,
            model=clean_model_name,
            temperature=0,
        )
        
    # 4. OpenAI (Default fallback)
    return ChatOpenAI(
        model=settings.MODEL,
        api_key=settings.OPENAI_API_KEY,
        temperature=0,
    )
