from django.db.models import (
    Min,
    Max,
    Sum,
    Prefetch,
)

from products.services.duplicate import DuplicateProductService
from products.services.match import ProductMatchService

from products.models import (
    AttributeValue,
    ProductDraftImage,
)


class DraftReviewPageService:
    """
    Step 5 önizleme ekranı için
    gerekli verileri hazırlar.

    Bu servis yalnızca ekranın ihtiyaç
    duyduğu bilgileri döndürür.

    Hiçbir kayıt oluşturmaz.
    """

    @staticmethod
    def get_context(
        *,
        draft,
    ):
        """
        Önizleme ekranı context'ini hazırlar.
        """

        variants = (
            draft.variants
            .filter(
                is_active=True,
            )
            .prefetch_related(
                Prefetch(
                    "attribute_values",
                    queryset=AttributeValue.objects.select_related(
                        "attribute",
                    ).order_by(
                        "attribute__name",
                        "value",
                    ),
                ),
            )
            .order_by(
                "sort_order",
                "id",
            )
        )

        images = (
            ProductDraftImage.objects
            .filter(
                group__draft=draft,
                is_active=True,
            )
            .select_related(
                "group",
            )
            .order_by(
                "-is_main",
                "sort_order",
                "id",
            )
        )

        stats = variants.aggregate(
            min_price=Min("price"),
            max_price=Max("price"),
            total_stock=Sum("stock"),
        )

        matched_product = DuplicateProductService.find_match(
            draft=draft,
        )

        match_result = None

        if matched_product:

            match_result = ProductMatchService.match(
                draft=draft,
                product=matched_product,
            )

        return {
            "draft": draft,
            "variants": variants,
            "images": images,
            "price_min": stats["min_price"],
            "price_max": stats["max_price"],
            "total_stock": stats["total_stock"] or 0,
            "matched_product": matched_product,
            "match_result": match_result,
        }