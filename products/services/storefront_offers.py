from django.db.models import (
    Case,
    DecimalField,
    F,
    IntegerField,
    OuterRef,
    Subquery,
    Value,
    When,
)


from collections import defaultdict
from decimal import Decimal

from products.models import (
    StoreProduct,
)


class StorefrontOfferService:
    """
    Müşteri vitrini için StoreProduct / BuyBox mantığını yönetir.

    ListView ve ProductDetailView aynı servisi kullanır.

    Şu anki strateji:
        first_created

    Daha sonra:
        lowest_price

    yapılabilir.

    Önemli:
    Bu servis sadece SATIN ALINABİLİR teklifleri dikkate alır.
    """

    # =========================================================
    # BUYBOX STRATEGY
    # =========================================================

    BUYBOX_STRATEGY = "first_created"

    # Kullanılabilecek stratejiler:
    #
    # "first_created"
    # "lowest_price"

    # =========================================================
    # BASE QUERYSET
    # =========================================================

    @staticmethod
    def purchasable_offers(*, product=None, variant=None):
        """
        Satın alınabilir StoreProduct tekliflerini döndürür.

        product verilirse:
            o ürünün tüm varyantlarının teklifleri

        variant verilirse:
            sadece o varyantın teklifleri
        """

        queryset = (
            StoreProduct.objects
            .purchasable()
            .select_related(
                "store",
                "variant",
                "variant__product",
            )
        )

        if product is not None:
            queryset = queryset.filter(
                variant__product=product,
            )

        if variant is not None:
            queryset = queryset.filter(
                variant=variant,
            )

        return queryset

    # =========================================================
    # ORDERING
    # =========================================================

    @classmethod
    def get_buybox_ordering(cls):
        """
        BuyBox algoritmasının ORM sıralamasını döndürür.

        Buradaki sıralama hem Detail hem List tarafında
        aynı mantığı kullanır.
        """

        if cls.BUYBOX_STRATEGY == "lowest_price":
            return (
                "price",
                "created_at",
                "id",
            )

        # Varsayılan:
        # İlk oluşturulan teklif kazanır.
        return (
            "created_at",
            "id",
        )

    # =========================================================
    # VARIANT SELECTION CONTEXT
    # =========================================================

    @classmethod
    def build_variant_selection_context(cls, *, product):
        """
        Detail sayfasındaki varyant seçim algoritmasının ihtiyaç
        duyduğu bütün veriyi tek seferde hazırlar.

        DB sorguları burada yapılır.
        Bundan sonra varyant seçim algoritmaları context üzerinden
        çalışır.
        """

        active_variants = list(
            getattr(product, "active_variants", [])
        )

        if not active_variants:
            return {
                "active_variants": [],
                "variant_data": [],
                "variant_by_id": {},
                "variant_value_ids_by_variant": {},
                "purchasable_offers": [],
                "owner_offer_by_variant": {},
                "lowest_offer_by_variant": {},
                "attribute_value_ids_by_attribute": {},
            }

        # -----------------------------------------------------
        # 1. VARIANT DATA
        # -----------------------------------------------------

        variant_data = []

        variant_by_id = {}

        variant_value_ids_by_variant = {}

        attribute_value_ids_by_attribute = defaultdict(set)

        for variant in active_variants:

            values = list(
                variant.attribute_values.all()
            )

            value_ids = {
                value.pk
                for value in values
            }

            variant_by_id[variant.pk] = variant

            variant_value_ids_by_variant[
                variant.pk
            ] = value_ids

            variant_data.append(
                {
                    "variant": variant,
                    "value_ids": value_ids,
                    "values": values,
                }
            )

            for value in values:
                attribute_value_ids_by_attribute[
                    value.attribute_id
                ].add(value.pk)

        # -----------------------------------------------------
        # 2. BÜTÜN SATIN ALINABİLİR OFFER'LAR
        # -----------------------------------------------------

        offers = list(
            cls.purchasable_offers(
                product=product,
            )
            .order_by(
                "variant_id",
                "price",
                "created_at",
                "id",
            )
        )

        # -----------------------------------------------------
        # 3. VARIANT -> EN UCUZ OFFER
        # -----------------------------------------------------

        lowest_offer_by_variant = {}

        for offer in offers:

            if offer.variant_id not in lowest_offer_by_variant:

                lowest_offer_by_variant[
                    offer.variant_id
                ] = offer

        # -----------------------------------------------------
        # 4. VARIANT -> OWNER OFFER
        # -----------------------------------------------------

        owner_offer_by_variant = {}

        owner_store_id = product.created_by_store_id

        if owner_store_id:

            for offer in offers:

                if offer.store_id != owner_store_id:
                    continue

                if offer.variant_id not in owner_offer_by_variant:

                    owner_offer_by_variant[
                        offer.variant_id
                    ] = offer

        # -----------------------------------------------------
        # CONTEXT
        # -----------------------------------------------------

        return {
            "active_variants": active_variants,

            "variant_data": variant_data,

            "variant_by_id": variant_by_id,

            "variant_value_ids_by_variant": (
                variant_value_ids_by_variant
            ),

            "purchasable_offers": offers,

            "owner_offer_by_variant": (
                owner_offer_by_variant
            ),

            "lowest_offer_by_variant": (
                lowest_offer_by_variant
            ),

            "attribute_value_ids_by_attribute": (
                dict(attribute_value_ids_by_attribute)
            ),
        }

    # =========================================================
    # VARIANT SELECTION
    # =========================================================

    @classmethod
    def get_initial_variant(cls, *, product, context):
        """
        Ürün ilk açıldığında gösterilecek varyantı belirler.

        Öncelik:

        1. Product.default_variant'ın aktif olması ve
           herhangi bir mağazada satın alınabilir olması.

        2. Default variant satın alınabilir değilse,
           ürünün satın alınabilir varyantları arasından
           en ucuz olan.

        3. Hiç satın alınabilir varyant yoksa,
           default variant veya ilk aktif variant.

        4. son fallback ilk aktif variant
        """

        active_variants = context["active_variants"]

        if not active_variants:
            return None

        variant_by_id = context["variant_by_id"]

        default_variant = variant_by_id.get(
            product.default_variant_id
        )

        # -----------------------------------------------------
        # 1. DEFAULT VARIANT
        # -----------------------------------------------------

        if default_variant:

            default_offer = context[
                "lowest_offer_by_variant"
            ].get(
                default_variant.pk
            )

            if default_offer:
                return default_variant

        # -----------------------------------------------------
        # 2. EN UCUZ SATIN ALINABİLİR VARIANT
        # -----------------------------------------------------

        lowest_offer_by_variant = context[
            "lowest_offer_by_variant"
        ]

        candidate_variants = []

        for variant_id, offer in lowest_offer_by_variant.items():

            variant = variant_by_id.get(variant_id)

            if not variant:
                continue

            candidate_variants.append(
                (
                    offer.price,
                    offer.created_at,
                    offer.pk,
                    variant,
                )
            )

        if candidate_variants:

            candidate_variants.sort(
                key=lambda item: (
                    item[0],
                    item[1],
                    item[2],
                )
            )

            return candidate_variants[0][3]

        # -----------------------------------------------------
        # 3. DEFAULT VARIANT
        # -----------------------------------------------------

        if default_variant:
            return default_variant

        # -----------------------------------------------------
        # 4. FALLBACK
        # -----------------------------------------------------

        return active_variants[0]

    @classmethod
    def get_variant_for_selection(
        cls,
        *,
        context,
        selected_variant=None,
        value_id=None,
    ):
        """
        Kullanıcı bir attribute value seçtiğinde hangi
        ProductVariant'a gidileceğini belirler.

        Öncelik:

        1. Seçilen kombinasyona uyan owner varyantı
        2. Owner yoksa en ucuz uygun varyant
        3. Hiç uygun varyant yoksa None

        ÖNEMLİ:
        Bu metodun içinde DB sorgusu YOKTUR.
        """

        if not value_id:
            return None

        variant_data = context["variant_data"]

        # -----------------------------------------------------
        # MEVCUT SEÇİM
        # -----------------------------------------------------

        selected_ids = set()

        if selected_variant:
            selected_ids = context[
                "variant_value_ids_by_variant"
            ].get(
                selected_variant.pk,
                set(),
            )

        # -----------------------------------------------------
        # Tıklanan value hangi attribute'a ait?
        # -----------------------------------------------------

        clicked_attribute_id = None

        for item in variant_data:

            for value in item["values"]:

                if value.pk == value_id:
                    clicked_attribute_id = value.attribute_id
                    break

            if clicked_attribute_id is not None:
                break

        if clicked_attribute_id is None:
            return None

        # -----------------------------------------------------
        # Aynı attribute grubundaki eski seçimi kaldır
        # -----------------------------------------------------

        group_value_ids = context[
            "attribute_value_ids_by_attribute"
        ].get(
            clicked_attribute_id,
            set(),
        )

        hypothetical_selection = (
            set(selected_ids)
            - group_value_ids
        )


        hypothetical_selection.add(value_id)

        # -----------------------------------------------------
        # Uygun variantları bul
        # -----------------------------------------------------

        candidates = []

        for item in variant_data:

            variant_value_ids = item["value_ids"]

            if not hypothetical_selection.issubset(
                variant_value_ids
            ):
                continue

            candidates.append(item)

        if not candidates:
            return None

        # -----------------------------------------------------
        # Owner + fiyat önceliği
        # -----------------------------------------------------

        owner_offer_by_variant = context[
            "owner_offer_by_variant"
        ]

        lowest_offer_by_variant = context[
            "lowest_offer_by_variant"
        ]

        # -----------------------------------------------------
        # 1. OWNER VARIANTLARI
        # -----------------------------------------------------

        owner_candidates = []

        for item in candidates:
            variant = item["variant"]
            owner_offer = owner_offer_by_variant.get(
                variant.pk
            )

            if not owner_offer:
                continue

            owner_candidates.append(
                (
                    owner_offer.price,
                    owner_offer.created_at,
                    owner_offer.pk,
                    variant,
                )
            )


        # -----------------------------------------------------
        # Owner varyant varsa owner kazanır
        # -----------------------------------------------------

        if owner_candidates:

            owner_candidates.sort(
                key=lambda item: (
                    item[0],
                    item[1],
                    item[2],
                )
            )

            return owner_candidates[0][3]

        # -----------------------------------------------------
        # 2. Owner yoksa en ucuz teklif
        # -----------------------------------------------------

        candidate_offers = []

        for item in candidates:

            variant = item["variant"]

            offer = lowest_offer_by_variant.get(
                variant.pk
            )

            if not offer:
                continue

            candidate_offers.append(
                (
                    offer.price,
                    offer.created_at,
                    offer.pk,
                    variant,
                )
            )

        if not candidate_offers:
            return None

        candidate_offers.sort(
            key=lambda item: (
                item[0],
                item[1],
                item[2],
            )
        )

        return candidate_offers[0][3]


    @classmethod
    def get_variant_offers_data_from_context(
        cls,
        *,
        variant,
        context,
        offer_id=None,
    ):
        """
        Daha önce build_variant_selection_context()
        tarafından çekilmiş offer listesini kullanır.

        DB query yapmaz.
        """

        if variant is None:
            return {
                "offers": [],
                "default_buybox": None,
                "cheapest_offer": None,
                "active_offer": None,
                "other_offers": [],
                "has_offers": False,
                "is_buybox_overridden": False
            }

        offers = [
            offer
            for offer in context["purchasable_offers"]
            if offer.variant_id == variant.pk
        ]


        if not offers:
            return {"offers": [], "default_buybox": None, "cheapest_offer": None, "active_offer": None, "other_offers": [], "has_offers": False, "is_buybox_overridden": False}


        # 1. EN UCUZ TEKLİFİ BUL (Rozet için hep gereklidir, stratejiden bağımsızdır)
        cheapest_offer = min(offers, key=lambda o: o.price)

        # 2. ÜRÜNÜN ASIL SAHİBİNİ BUL (Owner)
        owner_store_id = variant.product.created_by_store_id
        owner_offer = next((o for o in offers if o.store_id == owner_store_id), None)

        # 3. STRATEJİYE GÖRE DEFAULT BUYBOX'I BELİRLE
        if cls.BUYBOX_STRATEGY == "lowest_price":
            # Eğer sistem ileride "En ucuz olan kazansın" mantığına geçerse:
            default_buybox = cheapest_offer
        else:
            # Varsayılan strateji (first_created): Ürünün asıl sahibi (Owner) kazanır.
            # Asıl sahip yoksa (stok bittiyse vs.) en eski teklif kazanır.
            default_buybox = owner_offer if owner_offer else min(offers, key=lambda o: o.created_at)

        # 4. KULLANICI SEÇİMİ (Müşteri Diğer Satıcılardan birine tıkladı mı?)
        active_offer = default_buybox
        is_overridden = False

        if offer_id:
            try:
                offer_id = int(offer_id)
                selected_offer = next((o for o in offers if o.id == offer_id), None)
                
                # Seçilen satıcı varsa ve varsayılan BuyBox değilse
                if selected_offer and selected_offer.id != default_buybox.id:
                    active_offer = selected_offer
                    is_overridden = True
            except (ValueError, TypeError):
                pass

        # 5. DİĞER SATICILAR LİSTESİ (Ekranda gösterilen "active_offer" hariç herkes)
        other_offers = [o for o in offers if o.id != active_offer.id]

        # Diğer satıcıları UX açısından her zaman ucuzdan pahalıya sıralamak müşteriyi yormaz
        other_offers.sort(key=lambda o: (o.price, o.created_at))

        # KURAL: Eğer strateji "first_created" ise ve Asıl Sahip (Owner) diğer satıcılara düştüyse
        # onu listesinde en üste çivile (Çünkü o ürünün asıl sahibi). 
        # (Lowest Price stratejisinde buna gerek yoktur, ucuz olan en üsttedir).
        if cls.BUYBOX_STRATEGY == "first_created" and owner_offer and owner_offer in other_offers:
            other_offers.remove(owner_offer)
            other_offers.insert(0, owner_offer)

        return {
            "offers": offers,
            "default_buybox": default_buybox,
            "cheapest_offer": cheapest_offer,
            "active_offer": active_offer,
            "other_offers": other_offers,
            "has_offers": True,
            "is_buybox_overridden": is_overridden
        }

    # =========================================================
    # DETAIL - TEKLİFLER
    # =========================================================

    @classmethod
    def get_offers_for_variant(cls, *, variant):
        """
        Product Detail sayfasında seçili varyanta ait
        satın alınabilir teklifleri getirir.

        Örneğin:

            iPhone 17 Pro Max - Siyah

            Apple       80.000
            B Store     75.000
            C Store     82.000
        """

        if variant is None:
            return []

        return list(
            cls.purchasable_offers(
                variant=variant,
            ).order_by(
                *cls.get_buybox_ordering()
            )
        )

    @classmethod
    def get_variant_buybox(cls, *, variant):
        """
        Seçili varyantın BuyBox teklifini döndürür.
        """

        if variant is None:
            return None

        return (
            cls.purchasable_offers(
                variant=variant,
            )
            .order_by(
                *cls.get_buybox_ordering()
            )
            .first()
        )

    @classmethod
    def get_variant_offers_data(cls, *, variant):
        """
        Product Detail için:

            buybox
            other_offers
            offers
            has_offers

        döndürür.
        """

        offers = cls.get_offers_for_variant(
            variant=variant,
        )

        buybox = offers[0] if offers else None

        return {
            "offers": offers,
            "buybox": buybox,
            "other_offers": offers[1:] if offers else [],
            "has_offers": bool(offers),
        }

    # =========================================================
    # LIST - PRODUCT BUYBOX
    # =========================================================

    @classmethod
    def _get_buybox_base_subquery(cls, outer_ref_field="pk", use_default_variant=False):
        """
        Subquery'ler için temel (Base) QuerySet oluşturur.
        Dış sorgunun alan adını parametrik alır.
        
        use_default_variant=True ise: Dış (Outer) Product tablosundaki 'default_variant_id'ye 
        denk gelen teklifi bulmaya çalışır. Eğer yoksa (veya False ise) herhangi bir varyantın 
        en ucuz/ilk teklifini getirir.
        """
        qs = cls.purchasable_offers().filter(
            variant__product=OuterRef(outer_ref_field)
        )
        
        # EĞER varsayılan varyant zorunluluğu istenmişse:
        if use_default_variant:
            # Not: OuterRef ile dış tablonun default_variant_id'sini okuyoruz.
            qs = qs.filter(variant_id=OuterRef("default_variant_id"))
            
        # Owner teklifini öne al
        #
        # owner olmayan = 1
        # owner olan   = 0
        #
        # Böylece owner varsa her zaman önce gelir.

        qs = qs.annotate(
            owner_priority=Case(
                When(
                    store_id=OuterRef(
                        "created_by_store_id"
                    ),
                    then=Value(0),
                ),
                default=Value(1),
                output_field=IntegerField(),
            )
        )

        return qs.order_by(
            "owner_priority",
            *cls.get_buybox_ordering(),
        )

    @classmethod
    def get_product_buybox_subquery(cls, outer_ref_field="pk", use_default_variant=False):
        """
        ProductListView için Product başına seçilecek
        StoreProduct teklifinin fiyatını Subquery ile bulur.

        Bu metodun amacı N+1 oluşturmadan:

            Product
                ↓
            ilk / en ucuz StoreProduct
                ↓
            buybox_price

        elde etmektir.
        """

        return Subquery(
            cls._get_buybox_base_subquery(outer_ref_field, use_default_variant).values("price")[:1],
            output_field=DecimalField(max_digits=10, decimal_places=2),
        )

    @classmethod
    def get_product_buybox_store_subquery(cls, outer_ref_field="pk", use_default_variant=False):
        """
        ProductListView için BuyBox mağazasının ID'sini bulur.

        Böylece liste sayfasında örneğin:

            Apple

        bilgisini de gösterebiliriz.
        """

        return Subquery(
            cls._get_buybox_base_subquery(outer_ref_field, use_default_variant).values("store_id")[:1]
        )

    @classmethod
    def get_product_buybox_variant_subquery(cls, outer_ref_field="pk", use_default_variant=False):
        """
        ProductListView için BuyBox teklifinin hangi varyanta
        ait olduğunu bulur.

        Bu bilgi ileride liste kartında:
            "BuyBox hangi varyantta?"

        gibi işlemler için kullanılabilir.
        """

        return Subquery(
            cls._get_buybox_base_subquery(outer_ref_field, use_default_variant).values("variant_id")[:1]
        )

    # =========================================================
    # STRATEGY
    # =========================================================

    @classmethod
    def set_strategy(cls, strategy):
        """
        Runtime sırasında strateji değiştirmek gerekirse
        kullanılabilir.

        Normalde settings/config üzerinden yönetmek daha iyi olabilir.
        """

        allowed = {
            "first_created",
            "lowest_price",
        }

        if strategy not in allowed:
            raise ValueError(
                f"Geçersiz BuyBox stratejisi: {strategy}"
            )

        cls.BUYBOX_STRATEGY = strategy