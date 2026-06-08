"""Langfuse tracing initialization via OpenTelemetry + GoogleADKInstrumentor.

Call setup_tracing() once at application startup (after load_dotenv()).
Requires LANGFUSE_PUBLIC_KEY and LANGFUSE_SECRET_KEY in environment.

NOTE on PII: Google ADK spans include LLM prompt content, which in PRISMA
contains student PACI data (diagnoses, personal information). Ensure your
Langfuse project is configured with appropriate data retention and access
controls before enabling tracing in production.
"""

import logging
import os

logger = logging.getLogger(__name__)

_instrumented = False


def setup_tracing() -> bool:
    """Initialize Langfuse + GoogleADKInstrumentor. Idempotent, safe to call multiple times.

    Returns True if instrumentation was activated, False if keys are absent or setup failed.
    """
    global _instrumented
    if _instrumented:
        return True

    if not os.environ.get("LANGFUSE_PUBLIC_KEY") or not os.environ.get("LANGFUSE_SECRET_KEY"):
        logger.debug("Langfuse tracing disabled: LANGFUSE_PUBLIC_KEY/LANGFUSE_SECRET_KEY not set.")
        return False

    try:
        from openinference.instrumentation.google_adk import GoogleADKInstrumentor
        from langfuse import get_client

        # get_client() must run first — it registers the global OTEL tracer provider
        # that GoogleADKInstrumentor will attach to.
        client = get_client()
        GoogleADKInstrumentor().instrument()
        _instrumented = True

        try:
            if client.auth_check():
                logger.info("Langfuse tracing active — traces will appear at %s", _langfuse_host())
            else:
                logger.warning("Langfuse auth check failed — verify LANGFUSE_PUBLIC_KEY and LANGFUSE_SECRET_KEY.")
        except Exception as auth_exc:
            logger.warning(
                "Langfuse auth check failed (tracing may still work): %s", auth_exc
            )

        return True
    except ImportError as exc:
        logger.warning("Langfuse tracing unavailable (missing dependency): %s", exc)
        return False
    except Exception as exc:
        logger.warning("Langfuse tracing setup failed: %s", exc)
        return False


def _langfuse_host() -> str:
    return (
        os.environ.get("LANGFUSE_BASE_URL")
        or os.environ.get("LANGFUSE_HOST")
        or "https://cloud.langfuse.com"
    )
