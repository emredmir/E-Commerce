from django.db import transaction
from django.db.models import Prefetch, Max
from django.shortcuts import get_object_or_404
from itertools import product

from products.models import (
    AttributeValue,
    ProductDraftVariant,
    ProductDraft,
)


class DraftVariantService:
    """
    Wizard Step 2

    ProductDraftVariant işlemleri.

    Sorumlulukları
    --------------
    - varyant oluşturma
    - varyant silme
    - custom AttributeValue oluşturma
    - duplicate kontrolü
    - varyant listeleme
    """

    @staticmethod
    @transaction.atomic
    def create_variant(
        draft,
        form,
    ):

        attribute_values = (
            DraftVariantService.build_attribute_values(
                form
            )
        )


        if not attribute_values:
            raise ValueError(
                "En az bir varyant seçiniz."
            )


        variant_key = (
            DraftVariantService
            .build_variant_key(
                attribute_values
            )
        )


        existing_sets  = (
            DraftVariantService
            .get_existing_variant_sets(draft)
        )


        if variant_key in existing_sets:
            return {
                "created": False,
                "variant": None,
            }


        variant = (
            DraftVariantService
            .create_draft_variant(
                draft=draft,
                attribute_values=attribute_values,
            )
        )


        return {
            "created": True,
            "variant": variant,
        }
    
    @staticmethod
    def build_attribute_values(
        form,
    ):

        attribute_values = []


        for item in form.get_attribute_data():

            selected = item["selected"]

            custom = item["custom"]


            if selected:

                attribute_values.append(
                    selected
                )

                continue


            if custom:

                custom = " ".join(
                    custom.split()
                ).lower()


                value, created = AttributeValue.objects.get_or_create(
                    attribute=item["attribute"],
                    value=custom,
                    defaults={
                        "is_active": True
                    }
                )

                if not value.is_active:
                    value.is_active = True
                    value.save(update_fields=["is_active"])


                attribute_values.append(
                    value
                )


        return attribute_values
    
    @staticmethod
    def build_attribute_groups(
        form,
    ):
        """
            Bulk varyant oluşturma için
            attribute gruplarını hazırlar.

            Örnek dönüş:

            [
                {
                    "attribute": Renk,
                    "values": [
                        Siyah,
                        Beyaz,
                    ],
                },
                {
                    "attribute": Beden,
                    "values": [
                        M,
                        L,
                    ],
                }
            ]
            """

        groups = []


        for item in form.get_attribute_data():

            values = item["values"]


            if values:

                groups.append(
                    {
                        "attribute": item["attribute"],
                        "values": values,
                    }
                )


        return groups
    
    @staticmethod
    def generate_combinations(attribute_groups):
        """
        Attribute gruplarından tüm kombinasyonları üretir.
        """

        if not attribute_groups:
            return []

        value_lists = [
            group["values"]
            for group in attribute_groups
        ]

        return list(product(*value_lists))
    
    # @staticmethod
    # def is_duplicate_variant(
    #     draft,
    #     attribute_values,
    # ):
    #     """
    #     Aynı attribute kombinasyonu daha önce
    #     oluşturulmuş mu?
    #     """

    #     new_ids = {
    #         value.pk
    #         for value in attribute_values
    #     }

    #     variants = (
    #         ProductDraftVariant.objects
    #         .filter(
    #             draft=draft,
    #         )
    #         .prefetch_related(
    #             "attribute_values",
    #         )
    #     )

    #     for variant in variants:

    #         existing_ids = {
    #             value.pk
    #             for value in variant.attribute_values.all()
    #         }

    #         if existing_ids == new_ids:
    #             return True
    #             # raise ValueError(
    #             #     "Bu varyant zaten mevcut."
    #             # )

    #     return False

    # @staticmethod
    # def build_attribute_values(
    #     form,
    #     seller=None,
    # ):
    #     """
    #     Formdan AttributeValue listesini oluşturur.
    #     """

    #     attribute_values = []

    #     for item in form.get_attribute_data():

    #         attribute = item["attribute"]
    #         selected = item["selected"]
    #         custom = item["custom"]

    #         #
    #         # Mevcut değer
    #         #
    #         if selected:

    #             attribute_values.append(selected)

    #             continue

    #         #
    #         # Yeni değer
    #         #
    #         if custom:
    #             custom = " ".join(
    #                 custom.split().lower()
    #             )

    #             value, _ = AttributeValue.objects.get_or_create(
    #                 attribute=attribute,
    #                 value=custom,
    #             )

    #             attribute_values.append(value)

    #     return attribute_values

    @staticmethod
    @transaction.atomic
    def delete_variant(
        variant,
    ):
        """
        Draft varyantı siler.
        """

        variant.delete()

    @staticmethod
    def get_variants(
        draft,
    ):
        """
        Draft varyantlarını alfabetik döndürür.
        """
    
        variants = (
            ProductDraftVariant.objects
            .filter(
                draft=draft,
            )
            .prefetch_related(
                Prefetch(
                    "attribute_values",
                    queryset=(
                        AttributeValue.objects
                        .select_related("attribute")
                        .order_by(
                            "attribute__name",
                            "value",
                        )
                    ),
                )
            )
        )
    
        return sorted(
            variants,
            key=lambda variant: " ".join(
                value.value
                for value in variant.attribute_values.all()
            ).lower()
        )


    @staticmethod
    def has_variants(
        draft,
    ):
        """
        Draft içerisinde en az bir varyant var mı?
        """

        return (
            ProductDraftVariant.objects
            .filter(
                draft=draft,
            )
            .exists()
        )

    @staticmethod
    def variant_count(
        draft,
    ):
        """
        Draft varyant sayısını döndürür.
        """

        return (
            ProductDraftVariant.objects
            .filter(
                draft=draft,
            )
            .count()
        )
    
    @staticmethod
    def get_next_sort_order(draft):
        """
        Yeni varyant için kullanılacak
        sort_order değerini döndürür.
        """

        # 1. Draft kaydını veritabanı seviyesinde kilitliyoruz (Row-level lock).
        # Bu transaction bitene kadar aynı draft'a başka hiçbir istek varyant ekleyemez, kapıda bekletilir.
        ProductDraft.objects.select_for_update().get(pk=draft.pk)

        last_order = (
            ProductDraftVariant.objects
            .filter(draft=draft)
            .aggregate(
                max_order=Max("sort_order")
            )["max_order"]
        )

        return 0 if last_order is None else last_order + 1
    
    @staticmethod
    def get_variant(
        draft,
        variant_id,
    ):
        return get_object_or_404(
            ProductDraftVariant.objects.prefetch_related(
                "attribute_values",
            ),
            pk=variant_id,
            draft=draft,
        )
    
    @transaction.atomic
    @staticmethod
    def delete_all(
        draft,
    ):
        ProductDraftVariant.objects.filter(
            draft=draft
        ).delete()
    
    @staticmethod
    @transaction.atomic
    def create_variants(
        draft,
        form,
    ):
        """
        Bulk formdan gelen seçimlere göre
        tüm varyant kombinasyonlarını oluşturur.
        """


        attribute_groups = (
            DraftVariantService.build_attribute_groups(
                form
            )
        )
        
        combinations = (
            DraftVariantService.generate_combinations(
                attribute_groups
            )
        )
        
        if not combinations:
            raise ValueError(
                "En az bir varyant özelliği seçiniz."
            )


        # created = []
        # skipped = 0

        # for combination in product(*value_lists):

        #     variant = DraftVariantService.create_variant(
        #         draft=draft,
        #         attribute_values=list(combination),
        #     )

        #     if variant is None:
        #         skipped += 1
        #         continue
            
        #     created.append(variant)

        # return {
        #     "created": created,
        #     "skipped": skipped,
        # }

        existing_sets = (
            DraftVariantService.get_existing_variant_sets(
                draft
            )
        )

        created_variants = []
        skipped = 0

        for combination in combinations:
        
            variant_key = (
                DraftVariantService
                .build_variant_key(
                    combination
                )
            )

            if variant_key in existing_sets:
                skipped += 1
                continue
            
            variant = (
                DraftVariantService
                .create_draft_variant(
                    draft=draft,
                    attribute_values=combination,
                )
            )
            
            existing_sets.add(
                variant_key
            )
            created_variants.append(
                variant
            )



        return {
            "created": created_variants,
            "created_count": len(created_variants),
            "skipped_count": skipped,
        }
    
    @staticmethod
    def get_existing_variant_sets(
        draft,
    ):
        """
        Draft içerisindeki mevcut varyant
        kombinasyonlarını set olarak döndürür.
        """

        variants = (
            ProductDraftVariant.objects
            .filter(
                draft=draft,
            )
            .prefetch_related(
                "attribute_values",
            )
        )

        existing = set()

        for variant in variants:

            existing.add(
                DraftVariantService.build_variant_key(
                    variant.attribute_values.all()
                )
            )

        return existing
    
    @staticmethod
    def build_variant_key(
        attribute_values,
    ):
        """
        AttributeValue listesinden
        benzersiz varyant anahtarı üretir.
        """

        return tuple(
            sorted(
                (
                    value.attribute_id,
                    value.pk,
                )
                for value in attribute_values
            )
        )
    
    @staticmethod
    def create_draft_variant(
        draft,
        attribute_values,
    ):
        """
        ProductDraftVariant oluşturur
        ve attribute değerlerini bağlar.
        """

        variant = ProductDraftVariant.objects.create(
            draft=draft,
            sort_order=DraftVariantService.get_next_sort_order(
                draft
            ),
        )

        variant.attribute_values.set(
            attribute_values
        )

        return variant
    
