from django.http import JsonResponse
from django.template.loader import render_to_string
from django.shortcuts import get_object_or_404, render, redirect
from django.views import View
from django.urls import reverse
from django.db import transaction



from products.forms import (
    ImageGroupForm,
    ImageUploadForm,
    ImageUpdateForm,
)

from products.models import ProductDraft, ProductDraftImageGroup, ProductDraftImage
from products.services.image import DraftImageService, create_group, delete_group, upload_images, update_image, update_group, delete_image, reorder_images

from products.mixins import SellerRequiredMixin, StoreOwnerMixin, DraftWizardMixin

import logging
logger = logging.getLogger(__name__)


class ProductWizardStep3View(
    SellerRequiredMixin,
    StoreOwnerMixin,
    DraftWizardMixin,
    View,
):
    """
    Wizard Step 3

    Ürün görsellerinin ve görsel gruplarının
    yönetildiği ekran.
    """

    template_name = (
        "products/seller/wizard/step3.html"
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

        if draft.last_completed_step < 2:
            return redirect(
                "products:wizard_step2",
                store_slug=self.get_store().slug,
                draft_id=draft.pk,
            )

        DraftImageService.sync_draft_groups(draft=draft)
        

        groups = DraftImageService.get_groups(
            draft=draft,
        )

        group_form = ImageGroupForm(
            draft=draft,
        )

        upload_form = ImageUploadForm()

        # has_multiple_variants = draft.variants.filter(is_active=True).count() > 1
        has_multiple_variants = DraftImageService.get_visual_variant_count(draft=draft) > 1

        return render(
            request,
            self.template_name,
            {
                "store": self.get_store(),
                "draft": draft,
                "groups": groups,
                "group_form": group_form,
                "upload_form": upload_form,
                "has_multiple_variants": has_multiple_variants,
            },
        )

class ImageGroupCreateView(
    SellerRequiredMixin,
    StoreOwnerMixin,
    View,
):
    """
    Wizard Step 3

    AJAX ile yeni ProductDraftImageGroup oluşturur.
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

        try:
            draft = ProductDraft.objects.select_related(
                "category",
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
                    "message": "İşlem yapılmak istenen taslak bulunamadı.",
                },
                status=404,
            )

        form = ImageGroupForm(
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

            create_group(
                draft=draft,
                selections=form.cleaned_data["selections"],
            )

            groups = DraftImageService.get_groups(
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
                "Image group create failed. Draft=%s User=%s",
                draft.pk,
                request.user.pk,
            )

            return JsonResponse(
                {
                    "success": False,
                    "message":
                    "Görsel grubu oluşturulurken hata oluştu.",
                },
                status=500,
            )

        has_multiple_variants = draft.variants.filter(is_active=True).count() > 1

        html = render_to_string(
            "products/seller/wizard/image_group_list.html",
            {
                "groups": groups,
                "store": self.get_store(),
                "draft": draft,
                "group_form": ImageGroupForm(draft=draft),
                "has_multiple_variants": has_multiple_variants, 
            },
            request=request,
        )

        return JsonResponse(
            {
                "success": True,
                "message": "Görsel grubu oluşturuldu.",
                "html": html,
                "count": groups.count(),
            }
        )


class ImageGroupUpdateView(
    SellerRequiredMixin,
    StoreOwnerMixin,
    View,
):
    """
    Wizard Step 3

    AJAX ile ProductDraftImageGroup günceller.
    """

    http_method_names = [
        "post",
    ]

    def post(
        self,
        request,
        store_slug,
        draft_id,
        group_id,
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
            group = ProductDraftImageGroup.objects.select_related(
                "draft",
                "draft__category",
                "draft__seller",
                "draft__store",
            ).get(
                pk=group_id,
                draft_id=draft_id,
                draft__seller=request.user,
                draft__store=self.get_store(),
                draft__status=ProductDraft.Status.DRAFT,
            )
        except ProductDraftImageGroup.DoesNotExist:
            return JsonResponse(
                {
                    "success": False,
                    "message": "İşlem yapılmak istenen görsel grubu bulunamadı.",
                },
                status=404,
            )

        form = ImageGroupForm(
            request.POST,
            draft=group.draft,
            instance=group,
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

            update_group(
                group=group,
                selections=form.cleaned_data["selections"],
            )

            groups = DraftImageService.get_groups(
                draft=group.draft,
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
                "Image group update failed. Group=%s User=%s",
                group.pk,
                request.user.pk,
            )

            return JsonResponse(
                {
                    "success": False,
                    "message":
                    "Görsel grubu güncellenirken hata oluştu.",
                },
                status=500,
            )

        draft = group.draft

        has_multiple_variants = draft.variants.filter(is_active=True).count() > 1

        html = render_to_string(
            "products/seller/wizard/image_group_list.html",
            {
                "groups": groups,
                "store": self.get_store(),
                "draft": draft,
                "group_form": ImageGroupForm(draft=draft),
                "has_multiple_variants": has_multiple_variants, 
            },
            request=request,
        )

        return JsonResponse(
            {
                "success": True,
                "message": "Görsel grubu güncellendi.",
                "html": html,
                "count": groups.count(),
            }
        )

class ImageGroupDeleteView(
    SellerRequiredMixin,
    StoreOwnerMixin,
    View,
):
    """
    Wizard Step 3

    AJAX ile ProductDraftImageGroup siler.
    """

    http_method_names = [
        "post",
    ]

    def post(
        self,
        request,
        store_slug,
        draft_id,
        group_id,
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
            group = ProductDraftImageGroup.objects.select_related(
                "draft",
                "draft__category",
                "draft__seller",
                "draft__store",
            ).get(
                pk=group_id,
                draft_id=draft_id,
                draft__seller=request.user,
                draft__store=self.get_store(),
                draft__status=ProductDraft.Status.DRAFT,
            )
        except ProductDraftImageGroup.DoesNotExist:
            return JsonResponse(
                {
                    "success": False,
                    "message": "İşlem yapılmak istenen görsel grubu bulunamadı.",
                },
                status=404,
            )

        draft = group.draft

        try:

            delete_group(
                group=group,
            )

            groups = DraftImageService.get_groups(
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
                "Image group delete failed. Group=%s User=%s",
                group.pk,
                request.user.pk,
            )

            return JsonResponse(
                {
                    "success": False,
                    "message":
                    "Görsel grubu silinirken hata oluştu.",
                },
                status=500,
            )

        has_multiple_variants = draft.variants.filter(is_active=True).count() > 1

        html = render_to_string(
            "products/seller/wizard/image_group_list.html",
            {
                "groups": groups,
                "store": self.get_store(),
                "draft": draft,
                "group_form": ImageGroupForm(draft=draft),
                "has_multiple_variants": has_multiple_variants, 
            },
            request=request,
        )

        return JsonResponse(
            {
                "success": True,
                "message": "Görsel grubu silindi.",
                "html": html,
                "count": groups.count(),
            }
        )

class ImageUploadView(
    SellerRequiredMixin,
    StoreOwnerMixin,
    View,
):
    """
    Wizard Step 3

    AJAX ile ProductDraftImageGroup içerisine
    yeni görseller yükler.
    """

    http_method_names = [
        "post",
    ]

    def post(
        self,
        request,
        store_slug,
        draft_id,
        group_id,
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
            group = ProductDraftImageGroup.objects.select_related(
                "draft",
                "draft__category",
                "draft__seller",
                "draft__store",
            ).get(
                pk=group_id,
                draft_id=draft_id,
                draft__seller=request.user,
                draft__store=self.get_store(),
                draft__status=ProductDraft.Status.DRAFT,
            )
        except ProductDraftImageGroup.DoesNotExist:
            return JsonResponse(
                {
                    "success": False,
                    "message": "İşlem yapılmak istenen görsel grubu bulunamadı.",
                },
                status=404,
            )

        form = ImageUploadForm(
            request.POST,
            request.FILES,
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

            upload_images(
                group=group,
                files=form.cleaned_data["images"],
            )

            groups = DraftImageService.get_groups(
                draft=group.draft,
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
                "Image upload failed. Group=%s User=%s",
                group.pk,
                request.user.pk,
            )

            return JsonResponse(
                {
                    "success": False,
                    "message":
                    "Görseller yüklenirken hata oluştu.",
                },
                status=500,
            )

        draft = group.draft

        has_multiple_variants = draft.variants.filter(is_active=True).count() > 1

        html = render_to_string(
            "products/seller/wizard/image_group_list.html",
            {
                "groups": groups,
                "store": self.get_store(),
                "draft": draft,
                "group_form": ImageGroupForm(draft=draft),
                "has_multiple_variants": has_multiple_variants, 
            },
            request=request,
        )

        return JsonResponse(
            {
                "success": True,
                "message": "Görseller yüklendi.",
                "html": html,
                "count": groups.count(),
            }
        )

class ImageUpdateView(
    SellerRequiredMixin,
    StoreOwnerMixin,
    View,
):
    """
    Wizard Step 3

    AJAX ile ProductDraftImage günceller.
    """

    http_method_names = [
        "post",
    ]

    def post(
        self,
        request,
        store_slug,
        draft_id,
        image_id,
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
            image = ProductDraftImage.objects.select_related(
                "group",
                "group__draft",
                "group__draft__seller",
                "group__draft__store",
                "group__draft__category",
            ).get(
                pk=image_id,
                group__draft_id=draft_id,
                group__draft__seller=request.user,
                group__draft__store=self.get_store(),
                group__draft__status=ProductDraft.Status.DRAFT,
            )
        except ProductDraftImage.DoesNotExist:
            return JsonResponse(
                {
                    "success": False,
                    "message": "İlgili görsel bulunamadı veya daha önce silinmiş.",
                },
                status=404,
            )

        kwargs = {"image": image}

        if "alt_text" in request.POST:
            kwargs["alt_text"] = request.POST.get("alt_text", "")

        if "is_main" in request.POST:
            # Gelen string'i boolean'a çeviriyoruz
            is_main_str = request.POST.get("is_main", "")
            if is_main_str.lower() == "true":
                kwargs["is_main"] = True
            elif is_main_str.lower() == "false":
                kwargs["is_main"] = False


        draft = image.group.draft

        try:

            update_image(**kwargs)

            groups = DraftImageService.get_groups(
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
                "Image update failed. Image=%s User=%s",
                image.pk,
                request.user.pk,
            )

            return JsonResponse(
                {
                    "success": False,
                    "message":
                    "Görsel güncellenirken hata oluştu.",
                },
                status=500,
            )



        has_multiple_variants = draft.variants.filter(is_active=True).count() > 1

        html = render_to_string(
            "products/seller/wizard/image_group_list.html",
            {
                "groups": groups,
                "store": self.get_store(),
                "draft": draft,
                "group_form": ImageGroupForm(draft=draft),
                "has_multiple_variants": has_multiple_variants, 
            },
            request=request,
        )

        return JsonResponse(
            {
                "success": True,
                "message": "Görsel güncellendi.",
                "html": html,
                "count": groups.count(),
            }
        )

class ImageDeleteView(
    SellerRequiredMixin,
    StoreOwnerMixin,
    View,
):
    """
    Wizard Step 3

    AJAX ile ProductDraftImage siler.
    """

    http_method_names = [
        "post",
    ]

    def post(
        self,
        request,
        store_slug,
        draft_id,
        image_id,
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
            image = ProductDraftImage.objects.select_related(
                "group",
                "group__draft",
                "group__draft__seller",
                "group__draft__store",
                "group__draft__category",
            ).get(
                pk=image_id,
                group__draft_id=draft_id,
                group__draft__seller=request.user,
                group__draft__store=self.get_store(),
                group__draft__status=ProductDraft.Status.DRAFT,
            )
        except ProductDraftImage.DoesNotExist:
            return JsonResponse(
                {
                    "success": False,
                    "message": "İlgili görsel bulunamadı veya daha önce silinmiş.",
                },
                status=404,
            )

        draft = image.group.draft

        try:

            delete_image(
                image=image,
            )

            groups = DraftImageService.get_groups(
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
                "Image delete failed. Image=%s User=%s",
                image.pk,
                request.user.pk,
            )

            return JsonResponse(
                {
                    "success": False,
                    "message":
                    "Görsel silinirken hata oluştu.",
                },
                status=500,
            )

        has_multiple_variants = draft.variants.filter(is_active=True).count() > 1

        html = render_to_string(
            "products/seller/wizard/image_group_list.html",
            {
                "groups": groups,
                "store": self.get_store(),
                "draft": draft,
                "group_form": ImageGroupForm(draft=draft),
                "has_multiple_variants": has_multiple_variants, 
            },
            request=request,
        )

        return JsonResponse(
            {
                "success": True,
                "message": "Görsel silindi.",
                "html": html,
                "count": groups.count(),
            }
        )

class ImageReorderView(
    SellerRequiredMixin,
    StoreOwnerMixin,
    View,
):
    """
    Wizard Step 3

    AJAX ile bir görsel grubundaki
    görsellerin sıralamasını günceller.
    """

    http_method_names = [
        "post",
    ]

    def post(
        self,
        request,
        store_slug,
        draft_id,
        group_id,
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
            group = ProductDraftImageGroup.objects.select_related(
                "draft",
                "draft__category",
                "draft__seller",
                "draft__store",
            ).get(
                pk=group_id,
                draft_id=draft_id,
                draft__seller=request.user,
                draft__store=self.get_store(),
                draft__status=ProductDraft.Status.DRAFT,
            )
        except ProductDraftImageGroup.DoesNotExist:
            return JsonResponse(
                {
                    "success": False,
                    "message": "İşlem yapılmak istenen görsel grubu bulunamadı.",
                },
                status=404,
            )

        image_ids = request.POST.getlist(
            "image_ids[]"
        )

        try:

            image_ids = [
                int(pk)
                for pk in image_ids
            ]

        except ValueError:

            return JsonResponse(
                {
                    "success": False,
                    "message": "Geçersiz görsel sıralaması.",
                },
                status=400,
            )

        try:

            reorder_images(
                group=group,
                image_ids=image_ids,
            )

            groups = DraftImageService.get_groups(
                draft=group.draft,
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
                "Image reorder failed. Group=%s User=%s",
                group.pk,
                request.user.pk,
            )

            return JsonResponse(
                {
                    "success": False,
                    "message":
                    "Görseller sıralanırken hata oluştu.",
                },
                status=500,
            )

        draft = group.draft

        has_multiple_variants = draft.variants.filter(is_active=True).count() > 1

        html = render_to_string(
            "products/seller/wizard/image_group_list.html",
            {
                "groups": groups,
                "store": self.get_store(),
                "draft": draft,
                "group_form": ImageGroupForm(draft=draft),
                "has_multiple_variants": has_multiple_variants, 
            },
            request=request,
        )

        return JsonResponse(
            {
                "success": True,
                "message":
                "Görseller sıralandı.",
                "html": html,
            }
        )

class ProductWizardStep3CompleteView(
    SellerRequiredMixin,
    StoreOwnerMixin,
    View,
):
    """
    Wizard Step 3 tamamlanma işlemi.
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

        try:
            draft = ProductDraft.objects.select_related(
                "category",
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
                    "message": "İşlem yapılmak istenen taslak bulunamadı.",
                },
                status=404,
            )

        try:

            DraftImageService.validate_step3(
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

        if draft.last_completed_step >= 3:

            return JsonResponse(
                {
                    "success": True,
                    "redirect_url": reverse(
                        "products:wizard_step4",
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
                    3,
                )

                draft.current_step = max(
                    draft.current_step,
                    4,
                )

                draft.save(
                    update_fields=[
                        "last_completed_step",
                        "current_step",
                    ]
                )

        except Exception:

            logger.exception(
                "Wizard Step3 completion failed. Draft=%s User=%s",
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
                    "products:wizard_step4",
                    kwargs={
                        "store_slug": store_slug,
                        "draft_id": draft.pk,
                    },
                ),
            }
        )