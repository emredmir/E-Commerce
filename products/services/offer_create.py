from decimal import Decimal, InvalidOperation
from django.db import transaction

from products.models import ProductDraftVariant, ProductVariant
from products.services.variant import DraftVariantService
from products.services.offer_publish import OfferPublishService


class OfferCreateService:
    """
    Offer Wizard (Mevcut Ürüne Teklif Verme) işlemlerini yönetir.
    Ekranın verisini hazırlar, gelen verileri doğrular ve kaydeder.
    """

    @staticmethod
    def get_page_data(*, draft):
        """
        Offer ekranının ihtiyaç duyduğu tüm verileri tek seferde döndürür.
        View katmanını veritabanı sorgularından kurtarır.
        """
        product = draft.matched_product
        if not product:
            raise ValueError("Taslağa bağlı eşleşen bir katalog ürünü bulunamadı.")

        # 1. Katalogda halihazırda var olan varyantlar
        existing_catalog_variants = ProductVariant.objects.filter(
            product=product, 
            is_active=True
        ).prefetch_related("attribute_values")

        # 2. Satıcının bu taslak üzerinde daha önceden yaptığı işlemler (Yarım kalan form)
        draft_variants = draft.variants.filter(
            is_active=True
        ).prefetch_related("attribute_values")
        
        # Hızlı arama (O(1)) için taslak varyantlarını imzalarına göre sözlüğe al
        draft_lookup = {
            dv.attribute_signature: dv
            for dv in draft_variants
        }

        # --- GÖRSEL EŞLEŞTİRME İÇİN HAZIRLIK ---
        cat_image_groups = product.image_groups.filter(is_active=True).prefetch_related("visual_attribute_values", "images")
        draft_image_groups = draft.image_groups.filter(is_active=True).prefetch_related("visual_attribute_values", "images")

        def get_image_info(variant, image_groups):
            var_attrs = set(v.pk for v in variant.attribute_values.all())
            # Grupları, sahip oldukları özellik sayısına göre çoktan aza doğru sırala.
            # Böylece önce spesifik (örneğin Rengi kırmızı olan) gruplara bakılır,
            # eşleşme bulunamazsa en son ortak (özellik sayısı 0 olan) gruba bakılır.
            sorted_groups = sorted(
                image_groups, 
                key=lambda g: len(g.visual_attribute_values.all()), 
                reverse=True
            )

            for group in sorted_groups:
                group_attrs = set(v.pk for v in group.visual_attribute_values.all())
                
                if group_attrs.issubset(var_attrs):
                    # Prefetch cache'ini bozmamak için filtrelemeyi Python belleğinde yap
                    valid_images = [
                        img for img in group.images.all() 
                        if getattr(img, 'is_active', True)
                    ]
                    
                    if valid_images:
                        # Önce kapak olanı bul, yoksa listedeki ilk fotoğrafı al
                        main_img = next((img for img in valid_images if img.is_main), valid_images[0])
                        
                        return {
                            "url": main_img.image.url if main_img and main_img.image else None,
                            "count": len(valid_images)
                        }
                            
            return {"url": None, "count": 0}

        catalog_signatures = set()
        variants_context = []

        # A) Katalogdaki varyantları listeye ekle
        for cat_var in existing_catalog_variants:
            sig = cat_var.attribute_signature
            catalog_signatures.add(sig)
            img_info = get_image_info(cat_var, cat_image_groups)
            
            # Eğer satıcı daha önce fiyat girip sayfadan çıktıysa, draft_lookup içinde bulup formu dolu getireceğiz
            variants_context.append({
                "is_custom": False,
                "catalog_variant": cat_var,
                "draft_variant": draft_lookup.get(sig),
                "thumbnail_url": img_info["url"],
                "image_count": img_info["count"],
            })

        # B) Satıcının "Yeni Varyant Ekle" butonuyla eklediği katalog DIŞI varyantları listeye ekle
        for dv in draft_variants:
            sig = dv.attribute_signature
            if sig not in catalog_signatures:
                img_info = get_image_info(dv, draft_image_groups)
                variants_context.append({
                    "is_custom": True,
                    "catalog_variant": None,
                    "draft_variant": dv,
                    "thumbnail_url": img_info["url"],
                    "image_count": img_info["count"],
                })

        # --- ANA ÜRÜN GÖRSELİNİ BELİRLEME ---
        main_product_image_url = None

        # 1. Kural: Ortak grubun (hiçbir özelliğe bağlı olmayan) kapağını bul
        common_group = next((g for g in cat_image_groups if len(g.visual_attribute_values.all()) == 0), None)
        
        if common_group:
            valid_images = [img for img in common_group.images.all() if getattr(img, 'is_active', True)]
            if valid_images:
                main_img = next((img for img in valid_images if img.is_main), valid_images[0])
                if main_img and main_img.image:
                    main_product_image_url = main_img.image.url

        # 2. Kural: Eğer ortak grup yoksa veya boşsa, varsayılan varyantın kapağını bul
        if not main_product_image_url and product.default_variant:
            main_product_image_url = get_image_info(product.default_variant, cat_image_groups)

        return {
            "product": product,
            "variants_context": variants_context,
            # Hesapladığımız ana görsel url'sini template'e gönder
            "main_product_image_url": main_product_image_url, 
        }

        

    @staticmethod
    def validate(variants_data):
        """
        Frontend'den gelen teklif verilerini doğrular ve
        temizlenmiş (typed) veri döndürür.
        """

        if not variants_data:
            raise ValueError(
                "Lütfen en az bir varyant için teklif giriniz."
            )

        seen_skus = set()
        seen_barcodes = set()
        validated = []

        for v_data in variants_data:

            price = v_data.get("price")
            stock = v_data.get("stock")
            sku = (v_data.get("sku") or "").strip().lower()
            barcode = (v_data.get("barcode") or "").strip()

            # Fiyat ve stok boşsa bu varyant satılmayacak
            if (
                price is None
                or stock is None
                or str(price).strip() == ""
                or str(stock).strip() == ""
            ):
                continue

            try:
                price = Decimal(str(price))
            except (InvalidOperation, TypeError):
                raise ValueError(
                    "Geçersiz fiyat değeri."
                )

            try:
                stock = int(stock)
            except (TypeError, ValueError):
                raise ValueError(
                    "Geçersiz stok değeri."
                )

            if price <= 0:
                raise ValueError(
                    "Fiyat 0'dan büyük olmalıdır."
                )

            if stock < 0:
                raise ValueError(
                    "Stok değeri negatif olamaz."
                )

            sku_key = sku.lower()

            if sku_key:
                if sku_key in seen_skus:
                    raise ValueError(
                        "Aynı teklif içinde SKU değerleri benzersiz olmalıdır."
                    )

                seen_skus.add(sku_key)

            if barcode:
                if barcode in seen_barcodes:
                    raise ValueError(
                        "Aynı teklif içinde barkod değerleri benzersiz olmalıdır."
                    )
                seen_barcodes.add(barcode)


            try:
                variant_id = int(v_data["id"])
            except (KeyError, TypeError, ValueError):
                raise ValueError("Geçersiz varyant.")

            variant_type = v_data.get("type")

            if variant_type not in {"existing", "custom"}:
                raise ValueError("Geçersiz varyant tipi.")


            validated.append(
                {
                    "type": variant_type,
                    "id": variant_id,
                    "price": price,
                    "stock": stock,
                    "sku": sku or None,
                    "barcode": (
                        (v_data.get("barcode") or "").strip() or None
                    ),
                }
            )

        if not validated:
            raise ValueError(
                "Satışa başlamak için en az bir varyanta geçerli fiyat ve stok girmelisiniz."
            )

        for v_data in validated:
            barcode = v_data["barcode"]
            if barcode:
                qs = ProductVariant.objects.filter(barcode=barcode)
                # Eğer mevcut bir varyantsa (existing), kendini çakışmadan hariç tutuyoruz
                if v_data["type"] == "existing":
                    qs = qs.exclude(pk=v_data["id"])
                
                if qs.exists():
                    raise ValueError(f"BARCODE_CONFLICT||{barcode}||'{barcode}' barkodu sistemdeki başka bir ürüne ait. Lütfen kontrol ediniz.")

        return validated

    @staticmethod
    def _save_draft_variants(
        *,
        draft,
        variants_data,
    ):
        """
        Offer ekranından gelen teklifleri
        ProductDraftVariant kayıtlarına işler.
        """

        draft.variants.update(
            is_active=False,
        )

        #
        # Catalog variants
        #

        catalog_variants = list(
            ProductVariant.objects.filter(
                product=draft.matched_product,
                is_active=True,
            ).prefetch_related(
                "attribute_values",
            )
        )

        catalog_variants_by_pk = {
            variant.pk: variant
            for variant in catalog_variants
        }

        #
        # Draft variants
        #

        draft_variants = list(
            draft.variants.prefetch_related(
                "attribute_values",
            )
        )

        draft_variants_by_pk = {
            variant.pk: variant
            for variant in draft_variants
        }

        draft_variants_by_signature = {
            variant.attribute_signature: variant
            for variant in draft_variants
        }

        #
        # Save
        #
        updated_variants = []

        for data in variants_data:

            variant_type = data["type"]
            variant_id = data["id"]

            if variant_type == "existing":

                catalog_variant = catalog_variants_by_pk.get(
                    variant_id,
                )

                if catalog_variant is None:
                    raise ValueError(
                        "Varyant bulunamadı."
                    )

                signature = (
                    catalog_variant.attribute_signature
                )

                draft_variant = (
                    draft_variants_by_signature.get(
                        signature,
                    )
                )

                if draft_variant is None:
                    draft_variant = ProductDraftVariant.objects.create(
                        draft=draft,
                        sort_order=catalog_variant.sort_order,
                        price=data["price"],
                        stock=data["stock"],
                        sku=data["sku"],
                        barcode=data["barcode"] or catalog_variant.barcode,
                        is_active=True,
                    )

                    draft_variant.attribute_values.set(
                        catalog_variant.attribute_values.all()
                    )

                    draft_variants_by_signature[
                        signature
                    ] = draft_variant

                else:
                    draft_variant.price = data["price"]
                    draft_variant.stock = data["stock"]
                    draft_variant.sku = data["sku"]
                    draft_variant.barcode = (
                         data["barcode"]
                         or catalog_variant.barcode
                         )
                    draft_variant.is_active = True

                updated_variants.append(
                    draft_variant
                )

            else:

                draft_variant = draft_variants_by_pk.get(
                    variant_id,
                )

                if draft_variant is None:
                    raise ValueError(
                        "Taslak varyant bulunamadı."
                    )

                draft_variant.price = data["price"]
                draft_variant.stock = data["stock"]
                draft_variant.sku = data["sku"]
                draft_variant.barcode = data["barcode"]
                draft_variant.is_active = True

                updated_variants.append(
                    draft_variant
                )
        if updated_variants:
            ProductDraftVariant.objects.bulk_update(
                updated_variants,
                fields=[
                    "price",
                    "stock",
                    "sku",
                    "barcode",
                    "is_active",
                ],
            )

    @staticmethod
    @transaction.atomic
    def save(*, draft, variants_data):
        """
        Teklifleri doğrular,
        taslak varyantlarını günceller
        ve yayına alır.
        """

        validated_variants = OfferCreateService.validate(
            variants_data,
        )

        OfferCreateService._save_draft_variants(
            draft=draft,
            variants_data=validated_variants,
        )

        return OfferPublishService.publish(
            draft=draft,
        )