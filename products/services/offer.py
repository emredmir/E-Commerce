from django.db.models import Prefetch
from django.db import transaction

from products.models import (
    AttributeValue,
    ProductDraftVariant,
    ProductDraftImageGroup,
)


class DraftOfferService:

    @staticmethod
    def get_variants(
        *,
        draft,
    ):
        """
        Step 4 ekranında gösterilecek taslak varyantları döndürür.

        - Attribute değerlerini tek sorguda yükler.
        - Sıralamayı korur.
        """

        # 1. Varyantları ve özelliklerini tek sorguda çekiyoruz
        variants = list(
            draft.variants
            .filter(is_active=True)
            .prefetch_related(
                Prefetch(
                    "attribute_values",
                    queryset=AttributeValue.objects.select_related("attribute").order_by("attribute__name", "value"),
                ),
            )
            .order_by("sort_order", "id")
        )

        # 2. Görsel gruplarını, özelliklerini ve resimlerini tek sorguda RAM'e alıyoruz
        image_groups = list(
            ProductDraftImageGroup.objects.filter(draft=draft, is_active=True)
            .prefetch_related("visual_attribute_values", "images")
            .order_by("sort_order")
        )

        # 3. Grupları hızlı arama için ayırıyoruz (Ortak grup vs Özellikli Grup)
        default_group = None
        specific_groups = []
        
        for group in image_groups:
            # Prefetch edildiği için DB'ye gitmez, RAM'den okur
            v_attrs = set(val.id for val in group.visual_attribute_values.all())
            if not v_attrs:
                default_group = group
            else:
                specific_groups.append((v_attrs, group))

        # 4. In-Memory (RAM üzerinde) Hızlı Eşleştirme İşlemi
        for variant in variants:
            # Varyantın özellik ID'leri (Yine RAM'den)
            variant_attrs = set(val.id for val in variant.attribute_values.all())
            
            best_group = None
            
            # Önce görsel özelliği olan gruplarla eşleşiyor mu bak (Örn: Siyah)
            for group_attrs, group in specific_groups:
                if group_attrs.issubset(variant_attrs):
                    best_group = group
                    break
            
            # Eşleşmediyse ortak grubu (Varsayılan) kullan
            if not best_group:
                best_group = default_group

            # Eşleşen gruptan 'is_main' olan veya ilk görseli thumbnail olarak ata
            variant.thumbnail_url = None
            if best_group:
                images = list(best_group.images.all()) # RAM'den liste olarak al
                if images:
                    main_img = next((img for img in images if img.is_main), images[0])
                    variant.thumbnail_url = main_img.image.url

        # Geriye thumbnail_url propertysi eklenmiş varyant listesi dönüyoruz
        return variants

    @staticmethod
    def update_variants(
        *,
        draft,
        variants,
    ):
        """
        Step 4'te düzenlenen varyant teklif
        bilgilerini kaydeder.

        Parameters
        ----------
        draft : ProductDraft

        variants : iterable

            [
                {
                    "id": 1,
                    "price": Decimal(...),
                    "stock": 10,
                    "sku": "ABC-001",
                    "barcode": "8691234567890",
                    "is_default": True,
                },
                ...
            ]
        """

        draft_variants = {
            variant.pk: variant
            for variant in draft.variants.filter(
                is_active=True,
            )
        }

        updated_variants = []

        with transaction.atomic():

            for data in variants:

                variant = draft_variants.get(
                    data["id"],
                )

                if variant is None:
                    raise ValueError(
                        "Güncellenmek istenen varyant bulunamadı."
                    )


                variant.price = data["price"]
                variant.stock = data["stock"]
                variant.sku = data["sku"]
                variant.barcode = data["barcode"]
                variant.is_default = data["is_default"]

                updated_variants.append(
                    variant
                )

            ProductDraftVariant.objects.bulk_update(
                updated_variants,
                fields=[
                    "price",
                    "stock",
                    "sku",
                    "barcode",
                    "is_default",
                ],
            )

    @staticmethod
    def validate(
        draft,
    ):
        """
        Step 4 doğrulamaları.

        Kontrol edilenler:

        - En az 1 varyant olmalı.
        - Her varyantın fiyatı geçerli olmalı.
        - Barkod tekrar etmemeli.
        - SKU tekrar etmemeli.
        - Tek bir varsayılan varyant olmalı.
        """

        variants = list(
            draft.variants.filter(
                is_active=True,
            )
        )

        if not variants:
            raise ValueError(
                "En az bir varyant bulunmalıdır."
            )

        default_count = 0

        barcodes = set()
        skus = set()

        for variant in variants:

            #
            # Price
            #

            if variant.price is None:
                raise ValueError(
                    "Tüm varyantlar için fiyat girilmelidir."
                )

            if variant.price <= 0:
                raise ValueError(
                    "Fiyat 0'dan büyük olmalıdır."
                )

            #
            # Stock
            #

            if variant.stock is None:
                raise ValueError(
                    "Tüm varyantlar için stok girilmelidir."
                )

            if variant.stock < 0:
                raise ValueError(
                    "Stok negatif olamaz."
                )

            #
            # Default Variant
            #

            if variant.is_default:
                default_count += 1

            #
            # Barcode
            #

            if variant.barcode:

                barcode = variant.barcode.strip()

                if barcode in barcodes:
                    raise ValueError(
                        "Aynı barkod birden fazla varyantta kullanılamaz."
                    )

                barcodes.add(barcode)

            #
            # SKU
            #

            if variant.sku:

                sku = variant.sku.strip().lower()

                if sku in skus:
                    raise ValueError(
                        "Aynı SKU birden fazla varyantta kullanılamaz."
                    )

                skus.add(sku)

        #
        # Default Variant
        #

        if len(variants) == 1:

            if not variants[0].is_default:
                raise ValueError(
                    "Tek varyant varsayılan olarak seçilmelidir."
                )

        elif default_count != 1:

            raise ValueError(
                "Bir adet varsayılan varyant seçmelisiniz."
            )

    @staticmethod
    def complete(
        *,
        draft,
    ):
        """
        Step 4'ü tamamlar.

        Doğrulamalar başarılıysa wizard'ın
        son adımını tamamlanmış olarak işaretler.
        """

        DraftOfferService.validate(
            draft=draft,
        )

        if draft.last_completed_step >= 4:
            draft.current_step = 5
            draft.save(update_fields=["current_step"])
            return

        with transaction.atomic():

            draft.last_completed_step = 4
            draft.current_step = 5

            draft.save(
                update_fields=[
                    "last_completed_step",
                    "current_step",
                ]
            )