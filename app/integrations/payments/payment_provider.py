from typing import Dict, Any, Optional
from app.core.config import settings
from app.core.logging import logger


class PaymentProvider:
    """Stripe Payment Integration Provider."""

    @classmethod
    async def create_checkout_session(
        cls,
        customer_email: str,
        price_id: str,
        success_url: str,
        cancel_url: str
    ) -> Optional[Dict[str, Any]]:
        """Create a Stripe checkout session for subscription upgrades."""
        if settings.STRIPE_SECRET_KEY:
            try:
                import stripe
                stripe.api_key = settings.STRIPE_SECRET_KEY
                session = stripe.checkout.Session.create(
                    payment_method_types=["card"],
                    customer_email=customer_email,
                    line_items=[{"price": price_id, "quantity": 1}],
                    mode="subscription",
                    success_url=success_url,
                    cancel_url=cancel_url,
                )
                return {"checkout_url": session.url, "session_id": session.id}
            except Exception as e:
                logger.error(f"Stripe checkout error: {str(e)}")
                raise RuntimeError(f"Stripe API error: {str(e)}")

        return {
            "checkout_url": None,
            "session_id": None,
            "message": "STRIPE_SECRET_KEY is not configured in backend .env. Please set valid Stripe keys to process real credit card payments."
        }
