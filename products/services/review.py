from products.models import (
    ProductDraftImage,
    ProductDraftVariant,
)


class DraftReviewService:
    """
    Step 5 öncesi taslak doğrulama servisi.

    Bu servis yalnızca taslağın yayınlanmaya
    hazır olup olmadığını kontrol eder.

    Hiçbir kayıt oluşturmaz veya güncellemez.
    """

    @staticmethod
    def validate(
        *,
        draft,
    ):
        """
        Taslağın yayınlanmaya hazır olup olmadığını doğrular.
        """
        if draft.category is None:
            raise ValueError(
                "Ürün kategorisi seçilmelidir."
            )

        if draft.store is None:
            raise ValueError(
                "Mağaza bilgisi bulunamadı."
            )

        if draft.seller is None:
            raise ValueError(
                "Satıcı bilgisi bulunamadı."
            )

        if not draft.name or not draft.name.strip():
            raise ValueError(
                "Ürün adı boş olamaz."
            )

        #
        # Draft durumu
        #
        
        if draft.status != draft.Status.DRAFT:
            raise ValueError(
                "Bu taslak yayınlanamaz."
            )

        #
        # Wizard adımları
        #

        if draft.last_completed_step < 4:
            raise ValueError(
                "Ürün henüz tüm adımları tamamlamadı."
            )

        #
        # Aktif varyantlar
        #

        variants = list(
            ProductDraftVariant.objects.filter(
                draft=draft,
                is_active=True,
            )
        )

        if not variants:
            raise ValueError(
                "En az bir aktif varyant bulunmalıdır."
            )

        #
        # Varsayılan varyant
        #

        default_variants = [
            variant
            for variant in variants
            if variant.is_default
        ]

        if len(default_variants) != 1:
            raise ValueError(
                "Bir adet varsayılan varyant seçilmelidir."
            )

        #
        # Fiyat / Stok
        #
        seen_skus = set()
        seen_barcodes = set()


        for variant in variants:

            if variant.price is None or variant.price <= 0:
                raise ValueError(
                    "Tüm varyantlar için geçerli fiyat girilmelidir."
                )

            if variant.stock is None or variant.stock < 0:
                raise ValueError(
                    "Tüm varyantlar için geçerli stok girilmelidir."
                )

            if variant.sku:
                sku_lower = variant.sku.strip().lower()
                if sku_lower in seen_skus:
                    raise ValueError("Aynı SKU birden fazla varyantta kullanılamaz.")
                seen_skus.add(sku_lower)

            if variant.barcode:
                barcode_clean = variant.barcode.strip()
                if barcode_clean in seen_barcodes:
                    raise ValueError("Aynı Barkod birden fazla varyantta kullanılamaz.")
                seen_barcodes.add(barcode_clean)

        #
        # Görseller (group__draft kullanıldı)
        #

        images = ProductDraftImage.objects.filter(
            group__draft=draft,
            is_active=True,
        )

        if not images.exists():
            raise ValueError(
                "En az bir ürün görseli yüklenmelidir."
            )

        #
        # Kapak görseli (is_main kullanıldı)
        #

        if not images.filter(
            is_main=True,
        ).exists():
            raise ValueError(
                "Bir kapak görseli seçilmelidir."
            )

        return True