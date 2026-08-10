import json

from django.db import transaction

from products.models import (
    AttributeValue,
    CategoryAttribute,
    ProductDraftImage,
    ProductDraftImageGroup,
    ProductDraftVariant,
    ProductVariant,
)
from products.services.variant import DraftVariantService


class OfferCustomVariantService:
    """
    Offer ekranında katalogda bulunmayan yeni bir varyantın
    taslağa eklenmesini yönetir.

    Bu servis ProductVariant oluşturmaz.

    Sadece:

        ProductDraftVariant
        ProductDraftImageGroup
        ProductDraftImage

    kayıtlarını oluşturur.

    Gerçek katalog katkısı publish aşamasında
    VariantContributionService tarafından yapılır.
    """

    @staticmethod
    def _parse_attributes_data(attributes_data):
        """
        Attribute verisini normalize eder.

        JSON string geldiyse parse eder.
        Liste geldiyse doğrudan kullanır.
        """

        if isinstance(attributes_data, str):

            try:
                attributes_data = json.loads(
                    attributes_data
                )

            except json.JSONDecodeError:
                raise ValueError(
                    "Geçersiz özellik formatı."
                )

        if not isinstance(attributes_data, list):
            raise ValueError(
                "Özellik verisi geçersiz."
            )

        if not attributes_data:
            raise ValueError(
                "Lütfen yeni varyant için özellikleri seçin."
            )

        return attributes_data

    @staticmethod
    def _resolve_attribute_values(
        *,
        draft,
        attributes_data,
    ):
        """
        Gelen attribute/value bilgilerini gerçek
        AttributeValue kayıtlarına dönüştürür.

        Aynı zamanda hangi değerlerin görsel özelliğe
        ait olduğunu belirler.

        Returns
        -------
        tuple
            (
                attribute_values,
                visual_attribute_values,
            )
        """

        attribute_values = []
        visual_attribute_values = []

        seen_attribute_ids = set()

        
        # Bu kategoride görselleri etkileyen attribute'lar tek sorguda alınır
        

        visual_attribute_ids = set(
            CategoryAttribute.objects.filter(
                category=draft.category,
                is_visual=True,
            ).values_list(
                "attribute_id",
                flat=True,
            )
        )

        #
        # Kategoride kullanılabilen attribute'lar
        #

        category_attributes_map = {
            ca.attribute_id: ca.allow_custom_values
            for ca in CategoryAttribute.objects.filter(category=draft.category)
        }

        for item in attributes_data:

            if not isinstance(item, dict):
                raise ValueError(
                    "Geçersiz özellik verisi."
                )

            attribute_id = item.get(
                "attribute_id"
            )

            value_id = item.get(
                "value_id"
            )

            custom_value = item.get(
                "custom_val"
            )

            #
            # Attribute zorunlu
            #

            if not attribute_id:
                raise ValueError(
                    "Geçersiz özellik seçimi."
                )

            try:
                attribute_id = int(attribute_id)

            except (TypeError, ValueError):
                raise ValueError(
                    "Geçersiz özellik seçimi."
                )

            #
            # Attribute gerçekten bu kategoride
            # kullanılabiliyor mu?
            #

            if attribute_id not in category_attributes_map:
                raise ValueError(
                    "Bu özellik seçilen kategori için geçerli değil."
                )
            
            allow_custom = category_attributes_map[attribute_id]

            #
            # Aynı attribute iki kez gönderilmesin.
            #
            
            if attribute_id in seen_attribute_ids:
                raise ValueError(
                    "Aynı özellik birden fazla kez seçilemez."
                )

            seen_attribute_ids.add(
                attribute_id
            )

            #
            # Hem value_id hem custom_value
            # gönderilmesini engelle.
            #

            if value_id and custom_value:
                raise ValueError(
                    "Bir özellik için hem mevcut hem özel değer kullanılamaz."
                )

            #
            # Mevcut AttributeValue
            #

            if value_id:

                try:
                    value_id = int(value_id)

                except (TypeError, ValueError):
                    raise ValueError(
                        "Geçersiz özellik değeri."
                    )

                try:
                    value = AttributeValue.objects.get(
                        pk=value_id,
                        attribute_id=attribute_id,
                    )

                except AttributeValue.DoesNotExist:
                    raise ValueError(
                        "Seçilen özellik değeri bulunamadı."
                    )

            #
            # Yeni / özel AttributeValue
            #

            elif custom_value:

                if not allow_custom:
                    raise ValueError("Bu özellik için satıcı tarafından yeni değer eklenmesine izin verilmiyor.")

                custom_value = " ".join(
                    str(custom_value).split()
                ).strip().lower()

                if not custom_value:
                    raise ValueError(
                        "Özel özellik değeri boş olamaz."
                    )

                value, _ = (
                    AttributeValue.objects.get_or_create(
                        attribute_id=attribute_id,
                        value=custom_value,
                        defaults={
                            "is_active": True,
                        },
                    )
                )

                #
                # Daha önce pasif edilmişse tekrar aktif et.
                #

                if not value.is_active:

                    value.is_active = True

                    value.save(
                        update_fields=[
                            "is_active",
                        ]
                    )

            else:
                raise ValueError(
                    "Her özellik için bir değer seçilmelidir."
                )

            #
            # Pasif değer kullanılmasın.
            #

            if not value.is_active:
                raise ValueError(
                    "Seçilen özellik değeri aktif değil."
                )

            attribute_values.append(
                value
            )

            #
            # Görsel attribute mı?
            #

            if attribute_id in visual_attribute_ids:
                visual_attribute_values.append(
                    value
                )

        if not attribute_values:
            raise ValueError(
                "En az bir özellik seçilmelidir."
            )

        return (
            attribute_values,
            visual_attribute_values,
        )

    @staticmethod
    def _validate_duplicate(
        *,
        draft,
        attribute_values,
    ):
        """
        Yeni varyantın katalogda veya mevcut draft içinde
        daha önce bulunup bulunmadığını kontrol eder.

        Returns
        -------
        tuple
            (
                signature,
            )
        """

        signature = (
            DraftVariantService.build_variant_key(
                attribute_values
            )
        )

        #
        # 1. Katalog kontrolü
        #

        catalog_variants = (
            ProductVariant.objects
            .filter(
                product=draft.matched_product,
                is_active=True,
            )
            .prefetch_related(
                "attribute_values",
            )
        )

        catalog_signatures = {
            variant.attribute_signature
            for variant in catalog_variants
        }

        if signature in catalog_signatures:
            raise ValueError(
                "Bu varyant katalogda zaten mevcut. "
                "Mevcut varyant üzerinden teklif vermelisiniz."
            )

        #
        # 2. Draft kontrolü
        #

        draft_variants = (
            draft.variants
            .filter(
                is_active=True,
            )
            .prefetch_related(
                "attribute_values",
            )
        )

        draft_signatures = {
            variant.attribute_signature
            for variant in draft_variants
        }

        if signature in draft_signatures:
            raise ValueError(
                "Bu varyantı zaten eklediniz."
            )

        return signature

    @staticmethod
    def _create_draft_variant(
        *,
        draft,
        attribute_values,
    ):
        """
        ProductDraftVariant oluşturur.
        """

        sort_order = (
            DraftVariantService
            .get_next_sort_order(
                draft
            )
        )

        draft_variant = (
            ProductDraftVariant.objects.create(
                draft=draft,
                sort_order=sort_order,
                is_active=True,
            )
        )

        draft_variant.attribute_values.set(
            attribute_values
        )

        return draft_variant

    @staticmethod
    def _create_image_group(
        *,
        draft,
        visual_attribute_values,
        images,
    ):
        """
        Yeni varyanta ait ProductDraftImageGroup
        ve ProductDraftImage kayıtlarını oluşturur.

        Görsel yoksa None döndürür.
        """

        if not images:
            return None

        image_group = (
            ProductDraftImageGroup.objects.create(
                draft=draft,
                sort_order=(
                    DraftVariantService
                    .get_next_sort_order(draft)
                ),
                is_active=True,
            )
        )

        #
        # Görseli etkileyen attribute'lar
        # gruba bağlanır.
        #
        # Örneğin:
        #
        # Siyah + 128GB
        #
        # visual attribute = Siyah
        #
        # 128GB görsel grubuna eklenmez.
        #

        if visual_attribute_values:

            image_group.visual_attribute_values.set(
                visual_attribute_values
            )

        thumbnail_url = None

        for index, image_file in enumerate(images):

            draft_image = ProductDraftImage(
                group=image_group,
                image=image_file,
                is_main=(index == 0),
                sort_order=index,
            )

            draft_image.save()

            if index == 0:
                thumbnail_url = (
                    draft_image.image.url
                )

        return {
            "group": image_group,
            "thumbnail_url": thumbnail_url,
        }

    @staticmethod
    @transaction.atomic
    def add_custom_variant(
        *,
        draft,
        attributes_data,
        images=None,
    ):
        """
        Offer ekranından katalogda olmayan yeni bir
        varyant oluşturur.

        Akış:

            attributes_data
                    ->
            AttributeValue çözümleme
                    ->
            Duplicate kontrolü
                    ->
            ProductDraftVariant
                    ->
            ProductDraftImageGroup
                    ->
            ProductDraftImage

        ProductVariant oluşturmaz.
        """

        if draft is None:
            raise ValueError(
                "Taslak bulunamadı."
            )

        if draft.matched_product is None:
            raise ValueError(
                "Eşleşen katalog ürünü bulunamadı."
            )

        #
        # Attribute verisini normalize et
        #

        attributes_data = (
            OfferCustomVariantService
            ._parse_attributes_data(
                attributes_data
            )
        )

        #
        # AttributeValue kayıtlarını çöz
        #

        (
            attribute_values,
            visual_attribute_values,
        ) = (
            OfferCustomVariantService
            ._resolve_attribute_values(
                draft=draft,
                attributes_data=attributes_data,
            )
        )

        #
        # Duplicate kontrolü
        #

        signature = (
            OfferCustomVariantService
            ._validate_duplicate(
                draft=draft,
                attribute_values=attribute_values,
            )
        )

        #
        # Draft variant oluştur
        #

        draft_variant = (
            OfferCustomVariantService
            ._create_draft_variant(
                draft=draft,
                attribute_values=attribute_values,
            )
        )

        #
        # Görseller
        #

        image_result = (
            OfferCustomVariantService
            ._create_image_group(
                draft=draft,
                visual_attribute_values=(
                    visual_attribute_values
                ),
                images=images,
            )
        )

        thumbnail_url = None

        if image_result:
            thumbnail_url = (
                image_result["thumbnail_url"]
            )

        #
        # Frontend için attribute gösterimi
        #

        attributes_display = " / ".join(
            value.value
            for value in attribute_values
        )

        return {
            "id": draft_variant.pk,
            "type": "custom",
            "attributes_display": attributes_display,
            "attribute_signature": signature,
            "thumbnail_url": thumbnail_url,
        }