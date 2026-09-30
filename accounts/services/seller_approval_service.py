import logging

from accounts.models import SellerProfile
from orders.services.iyzico_submerchant import (
    IyzicoSubmerchantService,
)


logger = logging.getLogger(__name__)


class SellerApprovalService:
    """
    Seller platform onay sürecini yönetir.

    Akış:

        SellerProfile
            ↓
        Platform approval
            ↓
        iyzico submerchant onboarding
    """

    @classmethod
    def approve(
        cls,
        *,
        seller_profile_id,
    ):
        seller = cls._get_seller(
            seller_profile_id=seller_profile_id,
        )

        cls._approve_platform(
            seller=seller,
        )

        result = IyzicoSubmerchantService.create(
            seller_profile_id=seller.pk,
        )

        logger.info(
            "Seller approval completed. "
            "SellerProfile=%s User=%s IyzicoResult=%r",
            seller.pk,
            seller.user_id,
            result,
        )

        return result

    @staticmethod
    def _get_seller(
        *,
        seller_profile_id,
    ):
        return (
            SellerProfile.objects
            .select_related("user")
            .get(
                pk=seller_profile_id,
            )
        )

    @staticmethod
    def _approve_platform(
        *,
        seller,
    ):
        if seller.is_approved:
            return

        seller.is_approved = True

        seller.save(
            update_fields=[
                "is_approved",
            ]
        )