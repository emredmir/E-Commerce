from django.db import transaction

from products.models import (
    ProductDraft,
)
from products.utils import normalize_product_name

from products.services.duplicate import (
    DuplicateProductService,
)


class DraftCreateService:
    """
    Wizard Step 1

    ProductDraft oluşturur veya mevcut taslağı döndürür.
    """

    @staticmethod
    @transaction.atomic
    def create_or_get_draft(
        *,
        seller,
        store,
        form,
    ):
        """
        Aynı satıcının aynı ürüne ait aktif taslağı varsa onu döndürür.
        Yoksa yeni taslak oluşturur.
        """

        cleaned = form.cleaned_data
        normalized = normalize_product_name(cleaned["name"])

        draft, created = ProductDraft.objects.get_or_create(

            seller=seller,

            store=store,

            category=cleaned["category"],

            normalized_key=normalized["normalized_key"],

            status=ProductDraft.Status.DRAFT,

            defaults={

                "name": cleaned["name"],

                "brand": cleaned.get("brand"),

                "description": cleaned.get("description", ""),
                
                "normalized_name": normalized["normalized_name"],

                "normalized_key": normalized["normalized_key"],

                "tokens": normalized["tokens"],

                "current_step": 1,

                "last_completed_step": 0,

            },

        )

        #
        # Kullanıcının eski taslağı varsa
        #
        if not created:

            return draft, False

        #
        # Yeni oluşturulan taslak için
        # katalog eşleşmesini araştır.
        #
        matched = DuplicateProductService.find_match(
            draft=draft,
        )

        if matched:
            draft.matched_product = matched
            draft.match_status = (
                ProductDraft.MatchStatus.PENDING
            )

            draft.save(
                update_fields=[
                    "matched_product",
                    "match_status",
                ]
            )

        return draft, True
    
class DraftUpdateService:
    """
    Wizard Step 1

    Mevcut ProductDraft bilgilerini günceller.
    """

    @staticmethod
    @transaction.atomic
    def update(
        draft,
        form,
    ):
        
        cleaned = form.cleaned_data

        # Duplicate kontrolü gerekip gerekmediğini belirle
        

        should_rematch = (
            "name" in form.changed_data
            or "category" in form.changed_data
            or "brand" in form.changed_data
        )


        draft.name = cleaned["name"]
        draft.category = cleaned["category"]
        draft.brand = cleaned.get("brand")
        draft.description = cleaned.get(
            "description",
            "",
        )

        # normalize_* alanları model.save() içerisinde otomatik güncelleniyor
        
        draft.save()

        if should_rematch:

            matched = DuplicateProductService.find_match(
                draft=draft,
            )

            draft.matched_product = matched
            draft.published_product = None

            if matched:
                draft.match_status = (
                    ProductDraft.MatchStatus.PENDING
                )
            else:
                draft.match_status = (
                    ProductDraft.MatchStatus.NONE
                )

            draft.save(
                update_fields=[
                    "matched_product",
                    "match_status",
                    "published_product",
                ],
            )

        return draft