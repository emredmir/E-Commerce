from django.http import JsonResponse
from django.template.loader import render_to_string
from django.shortcuts import get_object_or_404, render, redirect
from django.views import View
from django.urls import reverse
from django.db import transaction

from products.forms import VariantAttributeForm, BulkVariantAttributeForm
from products.models import ProductDraft, ProductDraftVariant
from products.services.variant import DraftVariantService
from products.services.image import DraftImageService

from products.mixins import SellerRequiredMixin, StoreOwnerMixin

import logging
logger = logging.getLogger(__name__)

class VariantCreateView(
    SellerRequiredMixin,
    StoreOwnerMixin,
    View,
):
    """
    Wizard Step 2

    AJAX ile yeni ProductDraftVariant oluşturur.
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

        draft = get_object_or_404(
            ProductDraft.objects.select_related(
                "category",
                "seller",
                "store",
            ),
            pk=draft_id,
            seller=request.user,
            store=self.get_store(),
            status=ProductDraft.Status.DRAFT,
        )

        form = VariantAttributeForm(
            request.POST,
            draft=draft,
        )

        if not form.is_valid():

            return JsonResponse(
                {
                    "success": False,
                    "message": "Lütfen hataları düzeltin.",
                    "errors": form.errors,
                },
                status=400,
            )

        try:
            result = DraftVariantService.create_variant(
                draft=draft,
                form=form,
                
            )

            variants = DraftVariantService.get_variants(
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
                "Variant create failed"
            )

            return JsonResponse(
                {
                    "success":False,
                    "message":
                    "Varyant oluşturulurken hata oluştu."
                },
                status=500,
            )
        if result["created"]:
            message = "Varyant oluşturuldu."
        else:
            message = "Bu varyant zaten mevcut."

        html = render_to_string(
            "products/seller/wizard/variant_list.html",
            {
                "variants": variants,
                "store": self.get_store(),
            },
            request=request,
        )


        return JsonResponse(
            {
                "success": True,
                "message": message,
                "html": html,
                "count": len(variants),
            }
        )
    
class BulkVariantCreateView(
    SellerRequiredMixin,
    StoreOwnerMixin,
    View,
):
    """
    Wizard Step 2

    AJAX ile aynı anda birden fazla
    ProductDraftVariant oluşturur.
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

        draft = get_object_or_404(
            ProductDraft.objects.select_related(
                "category",
                "seller",
                "store",
            ),
            pk=draft_id,
            seller=request.user,
            store=self.get_store(),
            status=ProductDraft.Status.DRAFT,
        )

        form = BulkVariantAttributeForm(
            request.POST,
            draft=draft,
        )
        
        if not form.is_valid():

            return JsonResponse(
                {
                    "success": False,
                    "message": "Lütfen hataları düzeltin.",
                    "errors": form.errors,
                },
                status=400,
            )

        MAX_BULK_VARIANTS = 200
        combination_count = form.get_combination_count()

        if combination_count > MAX_BULK_VARIANTS:
            return JsonResponse(
                {
                    "success": False,
                    "message": (
                        f"Tek seferde en fazla {MAX_BULK_VARIANTS} varyant "
                        "oluşturabilirsiniz. Lütfen seçimlerinizi azaltın."
                    ),
                },
                status=400,
            )

        try:

            result = DraftVariantService.create_variants(
                draft=draft,
                form=form,
                
            )

            variants = DraftVariantService.get_variants(
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
                "Variant create failed"
            )

            return JsonResponse(
                {
                    "success":False,
                    "message":
                    "Varyant oluşturulurken hata oluştu."
                },
                status=500,
            )
        

        created = result["created_count"] 
        skipped = result["skipped_count"] 
        if created and skipped: 
            message = (
                f"{created} varyant oluşturuldu. "
                f"{skipped} varyant zaten mevcut olduğu için atlandı." )
        elif created:
            message = (
                f"{created} varyant oluşturuldu."
                )
        else: 
            message = (
                "Seçilen varyantların tamamı zaten mevcut."
                )
        html = render_to_string(
            "products/seller/wizard/variant_list.html",
            {
                "variants": variants,
                "store": self.get_store(),
            },
            request=request,
        )
        return JsonResponse(
            {
                "success": True,
                "message": message,
                "html": html,
                "count": len(variants),
                "created_count": created,
                "skipped_count": skipped,
            },
        )

class VariantDeleteView(
    SellerRequiredMixin,
    StoreOwnerMixin,
    View,
):
    """
    Wizard Step 2

    AJAX ile ProductVariant siler.
    """

    http_method_names = [
        "post",
    ]

    def post(
        self,
        request,
        store_slug,
        draft_id,
        variant_id,
    ):
        
        if request.headers.get("X-Requested-With") != "XMLHttpRequest":
            return JsonResponse(
                {
                    "success": False,
                    "message": "Geçersiz istek.",
                },
                status=400,
            )


        variant = get_object_or_404(
            ProductDraftVariant.objects.select_related(
                "draft",
                "draft__seller",
                "draft__store",
            ),
            pk=variant_id,
            draft_id=draft_id,
            draft__seller=request.user,
            draft__store=self.get_store(),
            draft__status=ProductDraft.Status.DRAFT,

        )

        draft = variant.draft

        try:

            DraftVariantService.delete_variant(
                variant,
            )

            DraftImageService.clean_orphaned_groups(draft=draft)

            variants = DraftVariantService.get_variants(
                draft,
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
                "Variant delete failed. Variant=%s User=%s",
                variant.pk,
                request.user.pk,
            )

            return JsonResponse(
                {
                    "success": False,
                    "message": "Varyant silinirken hata oluştu.",
                },
                status=500,
            )

        html = render_to_string(
            "products/seller/wizard/variant_list.html",
            {
                "variants": variants,
                "store": self.get_store(),
            },
            request=request,
        )

        return JsonResponse(
            {
                "success": True,
                "message": "Varyant silindi.",
                "html": html,
                "count": len(variants),
            }
        )
    
class VariantDeleteAllView(
    SellerRequiredMixin,
    StoreOwnerMixin,
    View,
):
    """
    Wizard Step 2
    AJAX ile taslağa ait TÜM ProductDraftVariant'ları tek seferde siler.
    """
    http_method_names = ["post"]

    def post(self, request, store_slug, draft_id):
        if request.headers.get("X-Requested-With") != "XMLHttpRequest":
            return JsonResponse({"success": False, "message": "Geçersiz istek."}, status=400)

        draft = get_object_or_404(
            ProductDraft,
            pk=draft_id,
            seller=request.user,
            store=self.get_store(),
            status=ProductDraft.Status.DRAFT,
        )

        try:
            # Taslağa ait tüm varyantları sil
            ProductDraftVariant.objects.filter(draft=draft).delete()

            # Varyant kalmadığı için ortak grup hariç tüm görselleri sil
            DraftImageService.clean_orphaned_groups(draft=draft)
            
            # Liste artık boş olduğu için boş dizi gönderiyoruz
            variants = []
            
            html = render_to_string(
                "products/seller/wizard/variant_list.html",
                {
                    "variants": variants,
                    "store": self.get_store(),
                },
                request=request,
            )

            return JsonResponse({
                "success": True,
                "message": "Tüm varyantlar başarıyla silindi.",
                "html": html,
                "count": 0,
            })
            
        except Exception:
            logger.exception("Bulk variant delete failed. Draft=%s", draft.pk)
            return JsonResponse({
                "success": False,
                "message": "Varyantlar silinirken bir hata oluştu."
            }, status=500)


class ProductWizardStep2View(
    SellerRequiredMixin,
    StoreOwnerMixin,
    View,
):
    """
    Wizard Step 2

    Draft varyantlarının yönetildiği ekran.
    """

    template_name = (
        "products/seller/wizard/step2.html"
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

        if draft.last_completed_step < 1:
            return redirect(
                "products:wizard_step1",
                store_slug=self.get_store().slug,
            )

        variant_form = VariantAttributeForm(
            draft=draft,
        )


        bulk_form = BulkVariantAttributeForm(
            draft=draft,
        )

        variants = DraftVariantService.get_variants(
            draft=draft,
        )

        variant_attribute_count = (
            draft.category.category_attributes
            .filter(
                is_variant=True,
            )
            .count()
        )

        show_bulk_tab = variant_attribute_count > 1

        return render(
            request,
            self.template_name,
            {
                "store": self.get_store(),
                "draft": draft,
                "variant_form": variant_form,
                "bulk_form": bulk_form,
                "variants": variants,
                "show_bulk_tab": show_bulk_tab,
            },
        )

    def get_draft(
        self,
        draft_id,
    ):

        return get_object_or_404(
            ProductDraft.objects.select_related(
                "category",
                "brand",
                "store",
                "seller",
            ),
            pk=draft_id,
            seller=self.request.user,
            store=self.get_store(),
            status=ProductDraft.Status.DRAFT,
        )

class ProductWizardStep2CompleteView(
    SellerRequiredMixin,
    StoreOwnerMixin,
    View,
):
    """
    Wizard Step 2 tamamlanma işlemi.
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

        draft = get_object_or_404(
            ProductDraft.objects.select_related(
                "store",
                "seller",
            ),
            pk=draft_id,
            seller=request.user,
            store=self.get_store(),
            status=ProductDraft.Status.DRAFT,
        )


        if not DraftVariantService.has_variants(
            draft
        ):

            return JsonResponse(
                {
                    "success": False,
                    "message":
                    "Devam etmek için en az bir varyant oluşturmalısınız.",
                },
                status=400,
            )
        
        if draft.last_completed_step >= 2:
            return JsonResponse(
                {
                    "success": True,
                    "redirect_url": reverse(
                        "products:wizard_step3",
                        kwargs={
                            "store_slug": store_slug,
                            "draft_id": draft.pk,
                        },
                    ),
                }
            )

        try:
            with transaction.atomic():
                draft.last_completed_step = max(
                    draft.last_completed_step,
                    2,
                )


                draft.current_step = max(
                    draft.current_step,
                    3,
                )

                draft.save(
                    update_fields=[
                        "last_completed_step",
                        "current_step",
                    ]
                )
        except Exception:
            logger.exception(
                "Wizard Step2 completion failed. Draft=%s User=%s",
                draft.pk,
                request.user.pk,
            )

            return JsonResponse(
                {
                    "success": False,
                    "message": (
                        "Adım tamamlanırken bir hata oluştu."
                    ),
                },
                status=500,
            )


        return JsonResponse(
            {
                "success": True,
                "redirect_url": reverse(
                    "products:wizard_step3",
                    kwargs={
                        "store_slug": store_slug,
                        "draft_id": draft.pk,
                    },
                ),
            }
        )