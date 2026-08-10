from django.shortcuts import (
    render,
    redirect,
)
from django.contrib import messages
from django.views import View
from django.http import JsonResponse
from django.urls import reverse

from products.mixins import (
    SellerRequiredMixin,
    StoreOwnerMixin,
    DraftWizardMixin,
)

from products.services.review import (
    DraftReviewService,
)
from products.services.review_page import (
    DraftReviewPageService,
)

from products.services.duplicate import DuplicateProductService

from products.services.publish import (
    DraftPublishService,
)

from products.models import (
    Product,
    ProductStatus,
)

import logging

logger = logging.getLogger(__name__)


class ProductWizardStep5View(
    SellerRequiredMixin,
    StoreOwnerMixin,
    DraftWizardMixin,
    View,
):
    """
    Wizard Step 5

    Ürün yayınlama önizleme ekranı.
    """

    template_name = (
        "products/seller/wizard/step5.html"
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

        #
        # Önceki adımlar tamamlanmış olmalı.
        #

        if draft.last_completed_step < 4:

            return redirect(
                "products:wizard_step4",
                store_slug=store_slug,
                draft_id=draft.pk,
            )

        #
        # Son kontroller
        #

        try:
            DraftReviewService.validate(draft=draft)
        except ValueError as exc:
            # Kullanıcıya hatayı gösterip Step 4'e geri at
            messages.error(request, str(exc)) 
            return redirect("products:wizard_step4", store_slug=store_slug, draft_id=draft.pk)

        context = (
            DraftReviewPageService.get_context(
                draft=draft,
            )
        )

        context.update(
            {
                "store": self.get_store(),
            }
        )

        return render(
            request,
            self.template_name,
            context,
        )





class ProductWizardPublishView(
    SellerRequiredMixin,
    StoreOwnerMixin,
    DraftWizardMixin,
    View,
):
    """
    Wizard Step 5

    Ürünü yayınlar.
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

        decision = request.POST.get(
            "decision"
        )

        existing_product = None
        force_create = False


        if decision == "use_existing":
        
            product_id = request.POST.get(
                "product_id"
            )

            if not product_id:
                return JsonResponse(
                    {
                        "success":False,
                        "message":"Ürün bilgisi eksik."
                    },
                    status=400,
                )


            existing_product = Product.objects.filter(
                pk=product_id,
                status=ProductStatus.ACTIVE,
            ).first()


            if not existing_product:
                return JsonResponse(
                    {
                        "success":False,
                        "message":"Ürün bulunamadı."
                    },
                    status=404,
                )

            match = DuplicateProductService.find_match(draft=draft)


            if not match or match.pk != existing_product.pk:
                return JsonResponse(
                    {
                        "success":False,
                        "message":"Geçersiz ürün seçimi."
                    },
                    status=400,
                )

        elif decision == "create_new":
            force_create = True

        if draft.last_completed_step < 4:

            return JsonResponse(
                {
                    "success": False,
                    "message": "Önce Step 4 tamamlanmalıdır.",
                },
                status=400,
            )

        try:
            result = DraftPublishService.publish(
                draft=draft,
                existing_product=existing_product,
                force_create_new=force_create,
            )


            if result.get("duplicate"):
            
                product = result["matched_product"]

                return JsonResponse(
                    {
                        "success": False,
                        "duplicate": True,
                        "draft_id": draft.pk,
                        "match": {
                            "id": product.pk,
                            "name": product.name,
                            "brand": product.brand.name if product.brand else "",
                            "category": product.category.name,
                        },
                    },
                    status=409,
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
                "Product publish failed. Draft=%s User=%s",
                draft.pk,
                request.user.pk,
            )

            return JsonResponse(
                {
                    "success": False,
                    "message": (
                        "Ürün yayınlanırken beklenmeyen "
                        "bir hata oluştu."
                    ),
                },
                status=500,
            )

        return JsonResponse(
            {
                "success": True,
                "message": (
                    "Yeni ürün başarıyla oluşturuldu."
                    if result["is_new_product"]
                    else "Mevcut ürüne teklif başarıyla eklendi."
                ),
                "is_new_product": result["is_new_product"],
                "redirect_url": reverse(
                    "products:store_product_list",
                    kwargs={
                        "store_slug": store_slug,
                    },
                ),
            }
        )