import logging

from django.contrib import admin
from django.contrib.auth.admin import UserAdmin
from django.utils.translation import gettext_lazy

from .forms import CustomUserCreationForm
from .models import CustomUser, SellerProfile, Address, IyzicoOnboardingStatus

from .services.seller_approval_service import (
    SellerApprovalService,
)


logger = logging.getLogger(__name__)


# ==============================================================================
# SELLER PROFILE ADMIN
# ==============================================================================

@admin.register(SellerProfile)
class SellerProfileAdmin(admin.ModelAdmin):
    list_display = (
        "user",
        "company_name",
        "is_approved",
        "iyzico_onboarding_status",
        "iyzico_submerchant_key",
        "created_at",
    )

    list_filter = (
        "is_approved",
        "iyzico_onboarding_status",
    )

    search_fields = (
        "user__email",
        "company_name",
        "iyzico_submerchant_external_id",
        "iyzico_submerchant_key",
    )

    readonly_fields = (
        "created_at",
        "iyzico_submerchant_external_id",
        "iyzico_submerchant_key",
        "iyzico_onboarding_started_at",
        "iyzico_onboarded_at",
        "iyzico_last_sync_at",
        "iyzico_last_error_code",
        "iyzico_last_error_message",
    )

    actions = [
        "approve_sellers",
    ]

    @admin.action(
        description=(
            "Seçilen satıcıları onayla ve "
            "iyzico onboarding başlat"
        )
    )
    def approve_sellers(
        self,
        request,
        queryset,
    ):
        approved_count = 0
        onboarded_count = 0
        already_active_count = 0
        failed_count = 0

        for seller in queryset.select_related("user"):

            try:
                # --------------------------------------------------------------
                # PLATFORM ONAY DURUMU
                # --------------------------------------------------------------

                was_approved = seller.is_approved

                # --------------------------------------------------------------
                # IYZICO ZATEN AKTİF Mİ?
                # --------------------------------------------------------------

                if (
                    seller.iyzico_onboarding_status
                    == IyzicoOnboardingStatus.ACTIVE
                    and seller.iyzico_submerchant_key
                ):
                    if not was_approved:
                        seller.is_approved = True

                        seller.save(
                            update_fields=[
                                "is_approved",
                            ]
                        )

                        approved_count += 1

                    already_active_count += 1
                    continue

                # --------------------------------------------------------------
                # APPROVAL + IYZICO ONBOARDING
                # --------------------------------------------------------------

                SellerApprovalService.approve(
                    seller_profile_id=seller.pk,
                )

                if not was_approved:
                    approved_count += 1

                onboarded_count += 1

                logger.info(
                    "Seller approved from admin. "
                    "SellerProfile=%s User=%s",
                    seller.pk,
                    seller.user_id,
                )

            except Exception as exc:
                failed_count += 1

                logger.exception(
                    "Seller approval failed from admin. "
                    "SellerProfile=%s User=%s",
                    seller.pk,
                    seller.user_id,
                )

                self.message_user(
                    request,
                    (
                        f"{seller.company_name or seller.user.email}: "
                        "iyzico onboarding işlemi başarısız oldu."
                    ),
                    level="ERROR",
                )

        # ----------------------------------------------------------------------
        # ÖZET MESAJ
        # ----------------------------------------------------------------------

        self.message_user(
            request,
            (
                f"İşlem tamamlandı. "
                f"Platform onayı: {approved_count}, "
                f"iyzico onboarding: {onboarded_count}, "
                f"zaten aktif: {already_active_count}, "
                f"başarısız: {failed_count}."
            ),
            level=(
                "SUCCESS"
                if failed_count == 0
                else "WARNING"
            ),
        )


# ==============================================================================
# ADDRESS INLINE
# ==============================================================================

class AddressInline(admin.TabularInline):
    model = Address
    extra = 1


# ==============================================================================
# CUSTOM USER ADMIN
# ==============================================================================

@admin.register(CustomUser)
class CustomUserAdmin(UserAdmin):
    add_form = CustomUserCreationForm
    model = CustomUser
    verbose_name_plural = "Addresses"

    list_display = (
        "email",
        "phone_number",
        "first_name",
        "last_name",
        "is_seller",
        "is_staff",
    )

    list_filter = (
        "is_staff",
        "is_superuser",
        "is_seller",
    )

    ordering = (
        "-date_joined",
    )

    search_fields = (
        "email",
        "phone_number",
        "first_name",
        "last_name",
    )

    readonly_fields = (
        "date_joined",
        "last_login",
    )

    fieldsets = (
        (
            gettext_lazy("Login info"),
            {
                "fields": (
                    "email",
                    "phone_number",
                    "password",
                )
            },
        ),
        (
            gettext_lazy("Personal info"),
            {
                "fields": (
                    "first_name",
                    "last_name",
                    "is_seller",
                )
            },
        ),
        (
            gettext_lazy("Permissions"),
            {
                "fields": (
                    "is_active",
                    "is_staff",
                    "is_superuser",
                    "groups",
                    "user_permissions",
                )
            },
        ),
        (
            gettext_lazy("Important dates"),
            {
                "fields": (
                    "last_login",
                    "date_joined",
                )
            },
        ),
    )

    add_fieldsets = (
        (
            None,
            {
                "classes": (
                    "wide",
                ),
                "fields": (
                    "email",
                    "phone_number",
                    "first_name",
                    "last_name",
                    "password1",
                    "password2",
                ),
            },
        ),
    )

    inlines = [
        AddressInline,
    ]


# ==============================================================================
# ADDRESS ADMIN
# ==============================================================================

@admin.register(Address)
class AddressAdmin(admin.ModelAdmin):
    list_display = (
        "user",
        "title",
        "full_name",
        "phone_number",
        "address_line1",
        "address_line2",
        "city",
        "state",
        "postal_code",
        "is_default",
        "created_at",
    )

    list_filter = (
        "is_default",
        "city",
        "state",
    )

    search_fields = (
        "user__email",
        "title",
        "full_name",
        "phone_number",
        "address_line1",
        "address_line2",
        "city",
        "state",
        "postal_code",
    )

    ordering = [
        "-is_default",
        "-created_at",
    ]