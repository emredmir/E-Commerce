from django.shortcuts import render, redirect
from django.views import View
from django.http import JsonResponse
from django.urls import reverse

from products.forms import VariantOfferForm
from products.mixins import (
    SellerRequiredMixin,
    StoreOwnerMixin,
    DraftWizardMixin,
)
from products.services.offer import DraftOfferService

import logging
logger = logging.getLogger(__name__)

class ProductWizardStep4View(
    SellerRequiredMixin,
    StoreOwnerMixin,
    DraftWizardMixin,
    View,
):
    """
    Wizard Step 4

    Varyant fiyat, stok, SKU,
    barkod ve varsayılan varyant
    seçim ekranı.
    """

    template_name = (
        "products/seller/wizard/step4.html"
    )

    def get(
        self,
        request,
        store_slug,
        draft_id,
    ):

        draft = self.get_draft(
            draft_id=draft_id,
        )

        if draft.last_completed_step < 3:
            return redirect(
                "products:wizard_step3",
                store_slug=self.get_store().slug,
                draft_id=draft.pk,
            )

        variants = DraftOfferService.get_variants(
            draft=draft,
        )

        variant_forms  = [
            VariantOfferForm(
                instance=variant,
                prefix=f"variant_{variant.pk}",
            )
            for variant in variants
        ]

        return render(
            request,
            self.template_name,
            {
                "store": self.get_store(),
                "draft": draft,
                "variants": zip(
                    variants,
                    variant_forms,
                ),
            },
        )

class ProductWizardStep4SaveView(
    SellerRequiredMixin,
    StoreOwnerMixin,
    DraftWizardMixin,
    View,
):
    """
    Wizard Step 4

    AJAX ile varyant teklif bilgilerini kaydeder.
    """

    http_method_names = [
        "post",
    ]

    def post(
        self,
        request,
        store_slug,
        draft_id,
    ):

        if request.headers.get("X-Requested-With") != "XMLHttpRequest":
            return JsonResponse(
                {
                    "success": False,
                    "message": "Geçersiz istek.",
                },
                status=400,
            )

        draft = self.get_draft(
            draft_id=draft_id,
        )

        if draft.last_completed_step < 3:
            return JsonResponse(
                {
                    "success": False,
                    "message": "Önce Step 3 tamamlanmalıdır.",
                },
                status=400,
            )

        variants = DraftOfferService.get_variants(
            draft=draft,
        )


        variant_forms  = [
            VariantOfferForm(
                request.POST,
                prefix=f"variant_{variant.pk}",
                instance=variant,
            )
            for variant in variants
        ]

        if not all(form.is_valid() for form in variant_forms):

            errors = {
                form.prefix: form.errors
                for form in variant_forms
                if form.errors
            }

            return JsonResponse(
                {
                    "success": False,
                    "message": "Lütfen hataları düzeltin.",
                    "errors": errors,
                },
                status=400,
            )

        try:

            DraftOfferService.update_variants(
                draft=draft,
                variants=[
                    {
                        "id": form.instance.pk,
                        "price": form.cleaned_data["price"],
                        "stock": form.cleaned_data["stock"],
                        "sku": form.cleaned_data["sku"],
                        "barcode": form.cleaned_data["barcode"],
                        "is_default": form.cleaned_data["is_default"],
                    }
                    for form in variant_forms
                ],
            )

        except ValueError as exc:

            return JsonResponse(
                {
                    "success": False,
                    "message": str(exc),
                },
                status=400,
            )

        except Exception:

            logger.exception(
                "Step4 save failed. Draft=%s User=%s",
                draft.pk,
                request.user.pk,
            )

            return JsonResponse(
                {
                    "success": False,
                    "message":
                    "Teklif bilgileri kaydedilirken hata oluştu.",
                },
                status=500,
            )

        return JsonResponse(
            {
                "success": True,
                "message":
                "Teklif bilgileri kaydedildi.",
            }
        )

class ProductWizardStep4CompleteView(
    SellerRequiredMixin,
    StoreOwnerMixin,
    DraftWizardMixin,
    View,
):
    """
    Wizard Step 4 tamamlanma işlemi.
    """

    http_method_names = [
        "post",
    ]

    def post(
        self,
        request,
        store_slug,
        draft_id,
    ):

        if request.headers.get("X-Requested-With") != "XMLHttpRequest":
            return JsonResponse(
                {
                    "success": False,
                    "message": "Geçersiz istek.",
                },
                status=400,
            )

        draft = self.get_draft(
            draft_id=draft_id,
        )

        if draft.last_completed_step < 3:
            return JsonResponse(
                {
                    "success": False,
                    "message": "Önce Step 3 tamamlanmalıdır.",
                },
                status=400,
            )

        try:

            DraftOfferService.complete(
                draft=draft,
            )

        except ValueError as exc:

            return JsonResponse(
                {
                    "success": False,
                    "message": str(exc),
                },
                status=400,
            )

        except Exception:

            logger.exception(
                "Wizard Step4 completion failed. Draft=%s User=%s",
                draft.pk,
                request.user.pk,
            )

            return JsonResponse(
                {
                    "success": False,
                    "message":
                    "Adım tamamlanırken bir hata oluştu.",
                },
                status=500,
            )

        return JsonResponse(
            {
                "success": True,
                "redirect_url": reverse(
                    "products:wizard_step5",
                    kwargs={
                        "store_slug": store_slug,
                        "draft_id": draft.pk,
                    },
                ),
            }
        )