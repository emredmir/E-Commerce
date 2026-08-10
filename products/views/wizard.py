import logging

from django.db import DatabaseError, IntegrityError, transaction
from django.shortcuts import render, get_object_or_404
from django.urls import reverse
from django.views import View
from django.http import JsonResponse, Http404

from products.services.create import DraftCreateService, DraftUpdateService
from products.services.image import DraftImageService

from products.forms import ProductWizardStep1Form, BrandRequestForm
from products.mixins import SellerRequiredMixin, StoreOwnerMixin
from products.models import Category, ProductDraft
from products.services.variant import DraftVariantService



logger = logging.getLogger(__name__)


class ProductWizardStep1View(
    SellerRequiredMixin,
    StoreOwnerMixin,
    View,
):
    """
    Ürün oluşturma sihirbazı - Adım 1

    Bu adımda ProductDraft oluşturulur veya mevcut taslak yüklenir.

    - Ürün adı
    - Açıklama
    - Kategori
    - Marka

    Kaydedilen bilgiler ProductDraft üzerinde tutulur.

    Henüz gerçek Product oluşturulmaz.

    Varyantlar, görseller ve teklifler sonraki adımlarda oluşturulur.
    """

    template_name = "products/seller/wizard/step1.html"

    def get(self, request, store_slug, draft_id= None, *args, **kwargs):
        draft = self.get_draft(draft_id) if draft_id else None

        form = self._get_form(
            request=request,
            instance=draft,
        )
            

        return self._render(
            request=request,
            form=form,
            draft=draft,
        )

    def post(self, request, store_slug, draft_id=None, *args, **kwargs):

        if request.headers.get("X-Requested-With") != "XMLHttpRequest":
            return JsonResponse(
                {
                    "success": False,
                    "message": "Geçersiz istek.",
                },
                status=400,
            )

        if not draft_id:
            draft_id = request.POST.get("draft_id")
        
        draft = None
        if draft_id:
            try:
                draft = self.get_draft(draft_id)
            except Http404:
                return JsonResponse(
                    {
                        "success": False,
                        "message": "İşlem yapılmak istenen taslak bulunamadı veya silinmiş.",
                    },
                    status=404,
                )


        confirm_category_change = (
            request.POST.get("confirm_category_change") == "1"
        )

        form = self._get_form(
            request=request,
            data=request.POST,
            instance=draft,
        )

        if not form.is_valid():
            return JsonResponse(
                {
                    "success": False,
                    "message": "Lütfen formdaki hataları düzeltin.",
                    "errors": form.errors,
                },
                status=400,
            )
        
        
        if draft:
            category_changed = "category" in form.changed_data

            has_variants = DraftVariantService.has_variants(draft)
            has_images = draft.image_groups.exists()

            needs_reset = has_variants or has_images
            

            if (
                category_changed
                and needs_reset
                and not confirm_category_change
            ):

                return JsonResponse(
                    {
                        "success": False,
                        "confirm_category_change": True,
                        "message": (
                            "Kategoriyi değiştirirseniz oluşturduğunuz "
                            "tüm varyantlar ve yüklediğiniz görseller silinecektir."
                        ),
                    },
                    status=409,
                )

        try:
            if draft:
                if (
                    category_changed
                    and needs_reset
                    and confirm_category_change
                ):
                    if has_variants:
                        DraftVariantService.delete_all(
                            draft=draft,
                        )
                    if has_images:
                        DraftImageService.delete_all_for_draft(draft=draft)

                draft = DraftUpdateService.update(
                    draft=draft,
                    form=form,
                )

                created = False

            else:
            
                draft, created = DraftCreateService.create_or_get_draft(
                    seller=request.user,
                    store=self.get_store(),
                    form=form,
                )

        except IntegrityError:
            logger.exception(
                "Draft create/get failed."
            )

            return JsonResponse(
                {
                    "success": False,
                    "message": (
                        "Ürün oluşturulurken veri çakışması oluştu. "
                        "Lütfen tekrar deneyin."
                    ),
                },
                status=400,
            )

        except DatabaseError:
            logger.exception(
                "DatabaseError while creating/loading draft."
            )

            return JsonResponse(
                {
                    "success": False,
                    "message": (
                        "Şu anda ürün oluşturulamıyor. "
                        "Lütfen daha sonra tekrar deneyin."
                    ),
                },
                status=500,
            )
        
        #
        # Aynı ürün katalogda bulundu.
        # Önce kullanıcıya sor.
        #


        if (
            draft.match_status
            == ProductDraft.MatchStatus.PENDING
            and draft.matched_product
        ):

            return JsonResponse(
                {
                    "success": True,
                    "duplicate": True,
                    "draft_id": draft.pk,

                    "match": {
                    "id": draft.matched_product.pk,
                    "name": draft.matched_product.name,
                    "brand": (
                        draft.matched_product.brand.name
                        if draft.matched_product.brand
                        else "-"
                    ),
                    "category": draft.matched_product.category.name,
                },
            }
        )

        #
        # Normal akış
        #

        draft.last_completed_step = max(
            draft.last_completed_step,
            1,
        )

        draft.current_step = max(
            draft.current_step,
            2,
        )

        draft.save(
            update_fields=[
                "last_completed_step",
                "current_step",
            ]
        )

        return JsonResponse(
            {
                "success": True,
                "duplicate": False,
                "created": created,
                "draft_id": draft.pk,
                "redirect_url": reverse(
                    "products:wizard_step2",
                    kwargs={
                        "store_slug": self.get_store().slug,
                        "draft_id": draft.pk,
                    },
                ),
            }
        )

    def _get_form(
        self,
        request,
        data=None,
        files=None,
        instance=None,
    ):

        return ProductWizardStep1Form(
            data=data,
            files=files,
            request=request,
            instance=instance,
        )

    def _render(
        self,
        request,
        form,
        draft=None,
    ):

        return render(
            request,
            self.template_name,
            {
                "form": form,
                "draft": draft,
                "store": self.get_store(),
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
                "seller",
                "store",
            ),
            pk=draft_id,
            seller=self.request.user,
            store=self.get_store(),
            status=ProductDraft.Status.DRAFT,
        )


class BrandRequestCreateView(SellerRequiredMixin, View):
    """
    Satıcının yeni marka talebi oluşturmasını sağlar.
    """

    def post(self, request, category_id):

        if request.headers.get("X-Requested-With") != "XMLHttpRequest":
            return JsonResponse(
                {
                    "success": False,
                    "message": "Geçersiz istek."
                },
                status=400,
            )
        try:
            category = Category.objects.get(
                pk=category_id,
                parent__isnull=False,
                is_active=True,
            )
        except Category.DoesNotExist:
            return JsonResponse(
                {
                    "success": False,
                    "message": "Seçilen alt kategori bulunamadı veya pasif durumda."
                },
                status=404,
            )

        form = BrandRequestForm(
            request.POST,
            seller=request.user,
            category=category,
        )

        if form.is_valid():

            try:
                brand_request = form.save(commit=False)
                brand_request.seller = request.user
                brand_request.category = category
                brand_request.save()
            except DatabaseError:
                logger.exception(
                    "Brand request creation failed. User=%s Category=%s",
                    request.user.pk,
                    category.pk,
                )

                return JsonResponse(
                    {
                        "success": False,
                        "message": "Talep oluşturulamadı.",
                    },
                    status=500,
                )
    
            return JsonResponse(
                {
                    "success": True,
                    "message": "Marka talebiniz yöneticilere gönderildi.",
                    "brand": brand_request.brand_name,
                }
            )

        return JsonResponse(
            {
                "success": False,
                "errors": form.errors,
            },
            status=400,
        )
    
class MatchDecisionView(
    SellerRequiredMixin,
    StoreOwnerMixin,
    View,
):
    """
    Kullanıcının katalog eşleşmesi hakkındaki
    kararını kaydeder.
    """

    http_method_names = [
        "post",
    ]

    DECISIONS = {
        "accept": ProductDraft.MatchStatus.ACCEPTED,
        "reject": ProductDraft.MatchStatus.REJECTED,
    }

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
        try:
            draft = ProductDraft.objects.select_related(
                    "matched_product",
                    "seller",
                    "store",
                ).get(
                    pk=draft_id,
                    seller=request.user,
                    store=self.get_store(),
                    status=ProductDraft.Status.DRAFT,
                )
        except ProductDraft.DoesNotExist:
            return JsonResponse(
                {
                    "success": False,
                    "message": "Değerlendirilecek ürün taslağı bulunamadı.",
                },
                status=404,
            )

        decision = request.POST.get("decision", "").strip().lower()

        if decision not in self.DECISIONS:

            return JsonResponse(
                {
                    "success": False,
                    "message": "Geçersiz seçim.",
                },
                status=400,
            )

        #
        # Bu eşleşme zaten değerlendirilmiş.
        #
        if (
            draft.match_status !=
            ProductDraft.MatchStatus.PENDING
        ):

            return JsonResponse(
                {
                    "success": False,
                    "message": "Bu eşleşme daha önce değerlendirildi.",
                },
                status=400,
            )


        update_fields = [
            "match_status",
        ]
        draft.match_status = self.DECISIONS[decision]
        #
        # Mevcut ürünü kullan
        #
        if decision == "accept":
            if draft.matched_product is None:
                return JsonResponse(
                    {
                        "success": False,
                        "message": "Eşleşen ürün bulunamadı.",
                    },
                    status=400,
                )
            redirect_url = reverse(
                "products:offer_create",
                kwargs={
                    "store_slug": store_slug,
                    "draft_id": draft.pk,
                },
            )
        #
        # Yeni ürün oluşturmaya devam et
        #
        else:
            draft.current_step = 2
            
            draft.last_completed_step = max(
                draft.last_completed_step,
                1,
            )
            update_fields.extend(
                [
                    "current_step",
                    "last_completed_step",
                ]
            )
            redirect_url = reverse(
                "products:wizard_step2",
                kwargs={
                    "store_slug": store_slug,
                    "draft_id": draft.pk,
                },
            )
        draft.save(
            update_fields=update_fields,
        )
        return JsonResponse(
            {
                "success": True,
                "redirect_url": redirect_url,
            }
        )