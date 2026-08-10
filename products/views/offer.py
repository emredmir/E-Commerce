from django.contrib import messages
from django.http import JsonResponse
from django.shortcuts import get_object_or_404, redirect, render
from django.urls import reverse
from django.db import IntegrityError
from django.views import View
import re

from products.models import ProductDraft, CategoryAttribute

from products.forms.offer import (
    OfferCreateForm,
    OfferCustomVariantForm,
)
from products.services.offer_create import (
    OfferCreateService,
)
from products.services.offer_custom_variant import (
    OfferCustomVariantService,
)

from products.mixins import (
    SellerRequiredMixin,
    StoreOwnerMixin,
)

import logging
logger = logging.getLogger(__name__)

class OfferCreateView(
    SellerRequiredMixin,
    StoreOwnerMixin,
    View,
):
    """
    Mevcut bir katalog ürününe satıcının
    teklif vermesini yönetir.

    GET:
        Offer ekranını gösterir.

    POST:
        Girilen teklifleri kaydeder ve
        StoreProduct kayıtlarını oluşturur/günceller.
    """

    template_name = "products/seller/offer_create.html"

    def get_store(self):
        return super().get_store()

    def get_draft(self):
        """
        Draft'ı seller + store üzerinden güvenli şekilde getirir.
        """

        store = self.get_store()

        return get_object_or_404(
            ProductDraft.objects.select_related(
                "matched_product",
                "category",
                "brand",
            ),
            pk=self.kwargs["draft_id"],
            seller=self.request.user,
            store=store,
        )

    def get(self, request, *args, **kwargs):

        draft = self.get_draft()

        #
        # Offer'a yalnızca eşleşmiş Product ile
        # gelinmesine izin veriyoruz.
        #

        if draft.match_status != ProductDraft.MatchStatus.ACCEPTED:
            messages.error(
                request,
                "Bu taslak için teklif oluşturulamaz.",
            )

            return redirect(
                reverse(
                    "products:wizard_step1",
                    kwargs={
                        "store_slug": self.kwargs["store_slug"],
                        "draft_id": draft.pk,
                    },
                )
            )

        if draft.matched_product is None:
            messages.error(
                request,
                "Eşleşen katalog ürünü bulunamadı.",
            )

            return redirect(
                reverse(
                    "products:wizard_step1",
                    kwargs={
                        "store_slug": self.kwargs["store_slug"],
                        "draft_id": draft.pk,
                    },
                )
            )

        page_data = (
            OfferCreateService.get_page_data(
                draft=draft,
            )
        )

        form = OfferCreateForm()

        context = {
            **page_data,
            "form": form,
            "draft": draft,
            "store": self.get_store(),
        }

        return render(
            request,
            self.template_name,
            context,
        )

    def post(self, request, *args, **kwargs):

        if request.headers.get("X-Requested-With") != "XMLHttpRequest":
            return JsonResponse({"success": False, "message": "Geçersiz istek."}, status=400)

        draft = self.get_draft()

        if draft.match_status != ProductDraft.MatchStatus.ACCEPTED or draft.matched_product is None:
            return JsonResponse({"success": False, "message": "Bu taslak için teklif oluşturulamaz."}, status=400)

        if draft.matched_product is None:
            return JsonResponse({"success": False, "message": "Eşleşen katalog ürünü bulunamadı."}, status=400)

        form = OfferCreateForm(
            request.POST,
        )

        if not form.is_valid():

            return JsonResponse({"success": False, "message": "Geçersiz teklif formatı."}, status=400)

        try:

            result = OfferCreateService.save(
                draft=draft,
                variants_data=(
                    form.cleaned_data[
                        "variants_data"
                    ]
                ),
            )

        except ValueError as exc:
            msg = str(exc)
            # Eğer kendi fırlattığımız "BARCODE_CONFLICT" ise parçalara ayır
            if msg.startswith("BARCODE_CONFLICT||"):
                parts = msg.split("||")
                return JsonResponse({
                    "success": False, 
                    "error_barcode": parts[1], # Hatalı barkod JS'e gidiyor
                    "message": parts[2]
                }, status=400)

            # Normal ValueError ise
            return JsonResponse({"success": False, "message": msg}, status=400)

        # Veritabanı Integrity (Benzersizlik) Hatalarını Yakala
        except IntegrityError as exc:
            error_msg = str(exc)
            if "barcode" in error_msg.lower():
                # SQL Server hata mesajından barkodu yakalamaya çalışıyoruz: "... The duplicate key value is (1114)."
                match = re.search(r'The duplicate key value is \((.*?)\)', error_msg)
                error_barcode = match.group(1) if match else None
                message = f"'{error_barcode}' barkodu katalogda zaten kayıtlı." if error_barcode else "Girdiğiniz barkodlardan biri sistemde zaten var."
                
                return JsonResponse({
                    "success": False,
                    "error_barcode": error_barcode,
                    "message": message
                }, status=400)
                
            return JsonResponse({"success": False, "message": "SKU veya benzeri bir veritabanı kısıtlaması ihlali oluştu."}, status=400)


        except Exception:
            logger.exception("Offer save failed. Draft=%s User=%s", draft.pk, request.user.pk)
            return JsonResponse({
                "success": False,
                "message": "Kayıt işlemi sırasında beklenmeyen bir hata oluştu."
            }, status=500)

        messages.success(
            request,
            "Teklifleriniz başarıyla kaydedildi.",
        )

        return JsonResponse({
            "success": True, 
            "message": "Teklifleriniz başarıyla kaydedildi.",
            "redirect_url": reverse("products:product_detail", kwargs={"slug": draft.matched_product.slug})
        })

class CategoryAttributesAPIView(View):
    def get(self, request, category_id, *args, **kwargs):
        # Kategoriye ait aktif özellikler (sıralamasına göre)
        category_attributes = CategoryAttribute.objects.filter(
            category_id=category_id
        ).select_related('attribute').prefetch_related('attribute__values')

        results = []
        for cat_attr in category_attributes:
            # Özelliğe ait aktif değerler
            active_values = cat_attr.attribute.values.filter(is_active=True)
            
            results.append({
                "id": cat_attr.attribute.id,
                "name": cat_attr.attribute.name,
                "allow_custom_values": cat_attr.allow_custom_values,
                "values": [
                    {
                        "id": val.id,
                        "value": val.value
                    } for val in active_values
                ]
            })

        return JsonResponse({"results": results})


class OfferCustomVariantCreateView(
    SellerRequiredMixin,
    StoreOwnerMixin,
    View,
):
    """
    Offer ekranından katalogda olmayan
    yeni bir varyant oluşturur.

    JSON response döndürür.
    """

    def get_draft(self):

        store = self.get_store()

        return get_object_or_404(
            ProductDraft.objects.select_related(
                "matched_product",
                "category",
            ),
            pk=self.kwargs["draft_id"],
            seller=self.request.user,
            store=store,
        )

    def post(self, request, *args, **kwargs):

        draft = self.get_draft()

        if draft.match_status != ProductDraft.MatchStatus.ACCEPTED:
            return JsonResponse(
                {
                    "success": False,
                    "message": (
                        "Bu taslak için varyant eklenemez."
                    ),
                },
                status=400,
            )

        if draft.matched_product is None:
            return JsonResponse(
                {
                    "success": False,
                    "message": (
                        "Eşleşen katalog ürünü bulunamadı."
                    ),
                },
                status=400,
            )

        form = OfferCustomVariantForm(
            request.POST,
            request.FILES,
        )

        if not form.is_valid():

            return JsonResponse(
                {
                    "success": False,
                    "errors": form.errors.get_json_data(),
                },
                status=400,
            )

        try:

            result = (
                OfferCustomVariantService
                .add_custom_variant(
                    draft=draft,
                    attributes_data=(
                        form.cleaned_data[
                            "attributes_data"
                        ]
                    ),
                    images=request.FILES.getlist(
                        "images"
                    ),
                )
            )

        except ValueError as exc:

            return JsonResponse(
                {
                    "success": False,
                    "message": str(exc),
                },
                status=400,
            )

        return JsonResponse(
            {
                "success": True,
                "variant": result,
            }
        )

