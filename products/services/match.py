from products.models import ProductVariant


class ProductMatchService:
    """
    Mevcut Product içindeki varyantları döndürür.

    Offer ekranında kullanılacaktır.
    """

    @staticmethod
    def get_product_variants(*, product):
        if product is None:
            raise ValueError(
                "Ürün bulunamadı."
            )

        return (
            ProductVariant.objects
            .filter(
                product=product,
                is_active=True,
            )
            .prefetch_related(
                "attribute_values",
            )
            .order_by(
                "sort_order",
                "id",
            )
        )

    @staticmethod
    def match(
        *,
        draft,
        product,
    ):
        """
        Taslak varyantlarını mevcut ProductVariant'larla eşleştirir.
        Step 5 (Review) ekranında kullanılmaktadır.
        """

        if product is None:
            raise ValueError(
                "Eşleştirilecek ürün bulunamadı."
            )

        existing_variants = list(
            ProductVariant.objects.filter(
                product=product,
                is_active=True,
            ).prefetch_related(
                "attribute_values",
            )
        )

        matched_variants = []

        new_variants = []

        
        # Draft varyantları
        

        draft_variants = (
            draft.variants
            .filter(
                is_active=True,
            )
            .prefetch_related(
                "attribute_values",
            )
        )

        
        # Signature bazlı eşleştirme
        

        existing_lookup = {
            variant.attribute_signature: variant
            for variant in existing_variants
        }

        for draft_variant in draft_variants:

            existing_variant = existing_lookup.get(
                draft_variant.attribute_signature,
            )

            if existing_variant:

                matched_variants.append(
                    {
                        "draft_variant": draft_variant,
                        "product_variant": existing_variant,
                    }
                )

            else:

                new_variants.append(
                    draft_variant
                )

        return {
            "product": product,
            "matched_variants": matched_variants,
            "new_variants": new_variants,
            "matched_count": len(matched_variants),
            "new_count": len(new_variants),
        }