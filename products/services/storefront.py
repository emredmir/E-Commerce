from collections import defaultdict
from django.core.paginator import Paginator
from django.db.models import Q, Prefetch, Exists, F, OuterRef
from django.db import IntegrityError, transaction
from django.core.exceptions import ObjectDoesNotExist

from products.services.storefront_offers import (
    StorefrontOfferService,
)

from products.models import (
    ProductQuestion,
    ProductAnswer,
    QuestionTopic,
    StoreProduct,
    Store,
    ProductQuestionUpvote,
)


class ProductDetailService:
    """
    Product Detail sayfasının business logic katmanı.

    Sorumlulukları:

    - Seçili varyantı belirlemek
    - Varyanta uygun görselleri belirlemek
    - StorefrontOfferService üzerinden teklifleri almak
    - BuyBox ve diğer satıcıları hazırlamak
    - Varyant seçeneklerini UI için hazırlamak
    """

    @classmethod
    def get_page_data(cls, *, product, variant_id=None, offer_id=None,):

        # =====================================================
        # 1. VARIANT SELECTION CONTEXT
        # =====================================================

        selection_context = (
            StorefrontOfferService
            .build_variant_selection_context(
                product=product,
            )
        )

        # =====================================================
        # 2. SELECTED VARIANT
        # =====================================================

        selected_variant = cls._select_variant(
            product=product,
            variant_id=variant_id,
            selection_context=selection_context,
        )

        # =====================================================
        # 3. OFFERS / BUYBOX
        # =====================================================

        offer_data = (
            StorefrontOfferService.get_variant_offers_data_from_context(
                variant=selected_variant,
                context=selection_context,
                offer_id=offer_id
            )
        )

        # =====================================================
        # 4. IMAGES
        # =====================================================

        display_images = cls._get_display_images(
            product=product,
            selected_variant=selected_variant,
        )

        # =====================================================
        # 5. VARIANT OPTIONS
        # =====================================================

        variant_attributes = cls._get_variant_attributes(
            product=product,
            selected_variant=selected_variant,
            selection_context=selection_context,
        )

        return {
            "product": product,
            "selected_variant": selected_variant,
            "display_images": display_images,
            
            "buy_box_offer": offer_data["active_offer"], # Ana ekranda kullanıcının seçtiği görünür
            "default_buybox": offer_data["default_buybox"], # URL'i temizlemek için asıl sahip lazım
            "cheapest_offer": offer_data["cheapest_offer"], # En Uygun Fiyat rozeti için
            "is_buybox_overridden": offer_data["is_buybox_overridden"], # Farklı satıcı uyarısı için
            
            "other_offers": offer_data["other_offers"],
            "offers": offer_data["offers"],
            "has_offers": offer_data["has_offers"],
            "variant_attributes": variant_attributes,
        }

    # =========================================================
    # VARIANT
    # =========================================================

    @staticmethod
    def _select_variant(*, product, variant_id=None, selection_context,):
        """
        Seçili ProductVariant'ı belirler.

        Öncelik:

        1. URL'den gelen aktif variant
        2. Product.default_variant
        3. En ucuz satın alınabilir variant
        4. Default / ilk aktif variant
        """

        active_variants = selection_context[
            "active_variants"
        ]
        if not active_variants:
            return None

        # 1. URL'den gelen (Kullanıcının özellikle tıkladığı) varyant
        if variant_id:
            selected = selection_context[
                "variant_by_id"
            ].get(
                int(variant_id)
                if str(variant_id).isdigit()
                else variant_id
            )

            if selected:
                return selected

        # 2. STOREFRONT BAŞLANGIÇ VARIANTI
        return StorefrontOfferService.get_initial_variant(
            product=product,
            context=selection_context,
        )

    # =========================================================
    # IMAGES
    # =========================================================

    @staticmethod
    def _get_display_images(*, product, selected_variant=None):
        """
        Seçili varyanta göre en spesifik ProductImageGroup'u bulur.

        Örnek:

            Variant:
                Siyah + 128GB

            Image Groups:
                Common
                Siyah
                Siyah + 128GB

        Sonuç:

            Siyah + 128GB

        Çünkü en fazla attribute ile eşleşen grup
        en spesifik gruptur.
        """

        image_groups = getattr(
            product,
            "cached_image_groups",
            [],
        )

        if not image_groups:
            return []

        # -----------------------------------------------------
        # Variant attribute ID'leri
        # -----------------------------------------------------

        variant_attribute_ids = set()

        if selected_variant:

            variant_attribute_ids = {
                value.pk
                for value in selected_variant.attribute_values.all()
            }

        common_images = []

        best_match_images = []

        best_match_count = -1

        # -----------------------------------------------------
        # Image Groups
        # -----------------------------------------------------

        for group in image_groups:

            group_attribute_ids = {
                value.pk
                for value in group.visual_attribute_values.all()
            }

            # -------------------------------------------------
            # Common Group
            # -------------------------------------------------

            if not group_attribute_ids:

                common_images = list(
                    group.images.all()
                )

                continue

            # Variant yoksa özel grup kullanma
            if not selected_variant:
                continue

            # -------------------------------------------------
            # Subset Matching
            # -------------------------------------------------

            if not group_attribute_ids.issubset(
                variant_attribute_ids
            ):
                continue

            match_count = len(group_attribute_ids)

            if match_count > best_match_count:

                best_match_count = match_count

                best_match_images = list(
                    group.images.all()
                )

        # -----------------------------------------------------
        # En spesifik grup
        # -----------------------------------------------------

        if best_match_images:
            # Varyanta özel resimleri başa koy, yanına ortak resimleri ekle
            return best_match_images + common_images

        # -----------------------------------------------------
        # Common
        # -----------------------------------------------------

        if common_images:
            return common_images

        # -----------------------------------------------------
        # Son fallback
        # -----------------------------------------------------

        for group in image_groups:

            images = list(
                group.images.all()
            )

            if images:
                return images

        return []

    # =========================================================
    # VARIANT ATTRIBUTES
    # =========================================================

    @classmethod
    def _get_variant_attributes(
        cls,
        *,
        product,
        selected_variant=None,
        selection_context,
    ):
        """
        Variant seçeneklerini UI için hazırlar.

        Her değer:

            id
            value
            is_selected
            is_available
            target_variant_id

        alanlarını içerir.

        target_variant_id, kullanıcının o değere tıklaması
        durumunda storefront algoritmasının seçtiği varianttır.
        """

        variant_data = selection_context[
            "variant_data"
        ]

        if not variant_data:
            return {}

        selected_ids = set()

        if selected_variant:

            selected_ids = {
                value.pk
                for value in selected_variant.attribute_values.all()
            }

        attributes = defaultdict(dict)

        # =====================================================
        # 1. ATTRIBUTE / VALUE'LARI TOPLA
        # =====================================================

        for item in variant_data:

            for value in item["values"]:

                attribute_name = value.attribute.name

                if value.pk not in attributes[attribute_name]:

                    attributes[attribute_name][value.pk] = {
                        "id": value.pk,
                        "value": value.value,
                        "is_selected": (
                            value.pk in selected_ids
                        ),
                        "is_available": False,
                        "target_variant_id": None,
                        "image_url": None,
                        "price": None,
                    }

        # =====================================================
        # 2. HER VALUE İÇİN HEDEF VARIANT'I BUL
        # =====================================================

        for attribute_name, values_dict in attributes.items():

            for value_id, data in values_dict.items():
                target_variant = None

                # ---------------------------------------------
                # Zaten seçiliyse
                # ---------------------------------------------

                if data["is_selected"]:

                    data["is_available"] = True

                    if selected_variant:
                        data["target_variant_id"] = selected_variant.pk
                        target_variant = selected_variant
                else:
                    # Kullanıcı bu value'ya tıklarsa hangi variant'a gider?
                    target_variant = StorefrontOfferService.get_variant_for_selection(
                        context=selection_context,
                        selected_variant=selected_variant,
                        value_id=value_id,
                    )
                    if target_variant:
                        data["is_available"] = True
                        data["target_variant_id"] = target_variant.pk

                # Eğer gidilecek bir varyant varsa, fotoğrafını ve o anki fiyatını çek.
                if target_variant:
                    # Fiyatı Bul
                    offer = selection_context["lowest_offer_by_variant"].get(target_variant.pk)
                    if offer:
                        data["price"] = offer.price
                    
                    # Resmi Bul
                    images = cls._get_display_images(product=product, selected_variant=target_variant)
                    if images:
                        data["image_url"] = images[0].image.url
        # =====================================================
        # 3. LIST'E ÇEVİR
        # =====================================================
        return {
            attribute_name: list(values.values())
            for attribute_name, values in attributes.items()
        }

class ProductQAService:
    """
    Product Detail Q&A business logic.

    Sorumlulukları:
    - Görünür ve cevaplanmış soruları listelemek.
    - Soru/cevap oluşturma ve temel validation işlemlerini yönetmek.
    - Soru/cevap görünürlüğünü ve silme işlemlerini yönetmek.
    - Upvote işlemlerini ve ilgili yetki kontrollerini yönetmek.
    - Topic, store, search, sort ve pagination işlemlerini yönetmek.

    Product Detail'da bir soru yalnızca:
        ProductQuestion.is_visible=True
        VE
        en az bir ProductAnswer.is_visible=True
    olduğunda gösterilir.

    Cevaplanma durumu ayrıca DB'de tutulmaz; gerçek cevap
    kayıtlarından türetilir.
    """

    PAGE_SIZE = 10

    ALLOWED_SORTS = {
        "-upvotes",
        "-created_at",
        "created_at",
    }

    # =====================================================
    # VISIBLE ANSWER CONDITION
    # =====================================================

    @staticmethod
    def _visible_answer_exists():
        """
        Outer question için en az bir görünür cevap
        olup olmadığını kontrol eden Exists expression.
        """

        return Exists(
            ProductAnswer.objects.filter(
                question_id=OuterRef("pk"),
                is_visible=True,
            )
        )

    # =====================================================
    # BASE QUESTION QUERYSET
    # =====================================================

    @classmethod
    def _visible_answered_questions(
        cls,
        *,
        product,
    ):
        """
        Product Detail'da gösterilebilecek sorular.

        Şartlar:

        1. Doğru ürün
        2. Soru görünür
        3. En az bir görünür cevap
        """

        visible_answers = Prefetch(
            "answers",
            queryset=(
                ProductAnswer.objects
                .filter(
                    is_visible=True,
                )
                .select_related(
                    "store",
                    "user",
                )
                .order_by(
                    "created_at",
                )
            ),
            to_attr="visible_answers",
        )

        return (
            ProductQuestion.objects
            .filter(
                product=product,
                is_visible=True,
            )
            .annotate(
                has_visible_answer=cls._visible_answer_exists(),
            )
            .filter(
                has_visible_answer=True,
            )
            .select_related(
                "user",
                "target_store",
            )
            .prefetch_related(
                visible_answers,
            )
        )

    # =====================================================
    # LIST / CONTEXT
    # =====================================================

    @classmethod
    def get_context(
        cls,
        *,
        product,
        request,
        offer_id=None,
    ):
        qa_sort = request.GET.get(
            "qa_sort",
            "-upvotes",
        )

        if qa_sort not in cls.ALLOWED_SORTS:
            qa_sort = "-upvotes"

        # -------------------------------------------------
        # TOPIC
        # -------------------------------------------------

        qa_topic = request.GET.get(
            "qa_topic",
            "all",
        )

        valid_topics = {
            value
            for value, _ in QuestionTopic.choices
        }

        if (
            qa_topic != "all"
            and qa_topic not in valid_topics
        ):
            qa_topic = "all"

        # -------------------------------------------------
        # STORE
        # -------------------------------------------------

        qa_store = request.GET.get(
            "qa_store",
            "all",
        )

        if qa_store != "all":
            if not str(qa_store).isdigit():
                qa_store = "all"

        # -------------------------------------------------
        # SEARCH
        # -------------------------------------------------

        qa_search = request.GET.get(
            "qa_search",
            "",
        ).strip()

        # -------------------------------------------------
        # CURRENT STORE
        # -------------------------------------------------

        current_store_id = cls._get_current_store_id(
            product=product,
            offer_id=offer_id,
        )

        # -------------------------------------------------
        # QUESTIONS
        # -------------------------------------------------

        questions = cls._get_questions(
            product=product,
            qa_sort=qa_sort,
            qa_topic=qa_topic,
            qa_store=qa_store,
            qa_search=qa_search,
        )

        # -------------------------------------------------
        # DYNAMIC TOPICS
        # -------------------------------------------------

        existing_topics = set(
            cls._visible_answered_questions(
                product=product,
            )
            .values_list(
                "topic",
                flat=True,
            )
        )

        dynamic_topics = [
            (value, label)
            for value, label in QuestionTopic.choices
            if value in existing_topics
        ]

        # -------------------------------------------------
        # DYNAMIC STORES
        # -------------------------------------------------

        existing_store_ids = (
            cls._visible_answered_questions(
                product=product,
            )
            .filter(
                target_store__isnull=False,
            )
            .values_list(
                "target_store_id",
                flat=True,
            )
            .distinct()
        )

        dynamic_stores = (
            Store.objects
            .filter(
                id__in=existing_store_ids,
            )
            .values(
                "id",
                "store_name",
            )
            .order_by(
                "store_name",
            )
        )

        # -------------------------------------------------
        # PAGINATION
        # -------------------------------------------------

        paginator = Paginator(
            questions,
            cls.PAGE_SIZE,
        )

        page_number = request.GET.get(
            "qa_page",
            1,
        )

        questions_page = paginator.get_page(
            page_number,
        )

        return {
            "questions": questions_page,
            "question_topics": dynamic_topics,
            "all_question_topics": QuestionTopic.choices,
            "question_stores": list(dynamic_stores),
            "qa_search_val": qa_search,
            "qa_topic": qa_topic,
            "qa_store": qa_store,
            "qa_sort": qa_sort,
            "qa_current_store_id": current_store_id,
        }

    # =====================================================
    # QUESTIONS
    # =====================================================

    @classmethod
    def _get_questions(
        cls,
        *,
        product,
        qa_sort="-upvotes",
        qa_topic="all",
        qa_store="all",
        qa_search="",
    ):
        questions = cls._visible_answered_questions(
            product=product,
        )

        # -------------------------------------------------
        # STORE FILTER
        # -------------------------------------------------

        if qa_store != "all":
            questions = questions.filter(
                target_store_id=int(qa_store),
            )

        # -------------------------------------------------
        # TOPIC FILTER
        # -------------------------------------------------

        if qa_topic != "all":
            questions = questions.filter(
                topic=qa_topic,
            )

        # -------------------------------------------------
        # SEARCH
        # -------------------------------------------------

        if qa_search:
            questions = questions.filter(
                Q(text__icontains=qa_search)
                |
                Q(
                    answers__text__icontains=qa_search,
                    answers__is_visible=True,
                )
            ).distinct()

        # -------------------------------------------------
        # SORT
        # -------------------------------------------------

        return questions.order_by(
            qa_sort,
            "-id",
        )

    # =====================================================
    # CURRENT STORE
    # =====================================================

    @staticmethod
    def _get_current_store_id(
        *,
        product,
        offer_id=None,
    ):
        if offer_id is None:
            return None

        try:
            offer_id = int(offer_id)
        except (TypeError, ValueError):
            return None

        return (
            StoreProduct.objects
            .filter(
                pk=offer_id,
                variant__product=product,
            )
            .values_list(
                "store_id",
                flat=True,
            )
            .first()
        )

    # =====================================================
    # CREATE QUESTION
    # =====================================================

    @staticmethod
    def create_question(
        *,
        product,
        user,
        topic,
        text,
        is_anonymous=False,
        offer_id=None,
        variant_id=None,
    ):
        valid_topics = {
            value
            for value, _ in QuestionTopic.choices
        }

        if topic not in valid_topics:
            raise ValueError(
                "Geçersiz soru konusu."
            )

        if not isinstance(text, str):
            raise ValueError(
                "Soru metni geçersiz."
            )

        text = text.strip()

        if not text:
            raise ValueError(
                "Soru metni boş olamaz."
            )

        if len(text) > 1000:
            raise ValueError(
                "Soru 1000 karakterden uzun olamaz."
            )

        if not isinstance(is_anonymous, bool):
            raise ValueError(
                "is_anonymous boolean olmalıdır."
            )

        target_store = None

        # -------------------------------------------------
        # VARIANT CONTEXT KONTROLÜ (YENİ)
        # -------------------------------------------------
        variant_context = None
        if variant_id is not None:
            try:
                variant_id = int(variant_id)
                # Ürüne ait öyle bir varyant var mı diye kontrol et
                variant_context = product.variants.get(id=variant_id)
            except (TypeError, ValueError, ObjectDoesNotExist):
                pass # Bulunamazsa None kalır, hata fırlatmaya gerek yok.

        # -------------------------------------------------
        # OFFER / STORE
        # -------------------------------------------------

        if offer_id is not None:
            try:
                offer_id = int(offer_id)
            except (TypeError, ValueError):
                raise ValueError(
                    "Geçersiz teklif ID."
                )

            offer = (
                StoreProduct.objects
                .select_related("store")
                .filter(
                    pk=offer_id,
                    variant__product=product,
                )
                .first()
            )

            if not offer:
                raise ValueError(
                    "Geçersiz ürün teklifi."
                )

            target_store = offer.store

        # -------------------------------------------------
        # CREATE
        # -------------------------------------------------

        return ProductQuestion.objects.create(
            product=product,
            user=user,
            target_store=target_store,
            variant_context=variant_context,
            topic=topic,
            text=text,
            is_anonymous=is_anonymous,
            is_visible=True,
        )

    # =====================================================
    # CREATE ANSWER
    # =====================================================

    @staticmethod
    @transaction.atomic
    def create_answer(
        *,
        question_id,
        store,
        user,
        text,
    ):
        """
        Yeni görünür cevap oluşturur.

        Cevap oluşturulduğu anda:

            answer.is_visible=True

        olduğu için soru Product Detail'da otomatik olarak
        görünür hale gelir.

        Burada ayrıca question state'i değiştirilmez.
        """

        # =====================================================
        # INPUT VALIDATION
        # =====================================================

        if not isinstance(text, str):
            raise ValueError(
                "Cevap metni geçersiz."
            )

        text = text.strip()

        if not text:
            raise ValueError(
                "Cevap metni boş olamaz."
            )

        if len(text) > 2000:
            raise ValueError(
                "Cevap 2000 karakterden uzun olamaz."
            )

        # =====================================================
        # USER / STORE AUTHORIZATION
        # =====================================================
        if not user or not user.is_authenticated:
            raise PermissionError(
                "Giriş yapmanız gerekiyor."
            )

        if not hasattr(user, 'seller_profile') or user.seller_profile.id != store.seller_id:
            raise PermissionError(
                "Bu mağazada cevap verme yetkiniz yok."
            )

        # =====================================================
        # LOCK QUESTION
        # =====================================================

        try:
            question = (
                ProductQuestion.objects
                .select_for_update()
                .get(
                    pk=question_id,
                    is_visible=True,
                )
            )
        except ProductQuestion.DoesNotExist:
            raise ValueError(
                "Soru bulunamadı veya artık görünür değil."
            )

        # =====================================================
        # STORE TARGET AUTHORIZATION
        # =====================================================

        # Belirli mağazaya sorulduysa sadece o mağaza cevaplayabilir.
        #
        # target_store=NULL ise tüm mağazalar cevaplayabilir.

        if (
            question.target_store_id is not None
            and question.target_store_id != store.id
        ):
            raise PermissionError(
                "Bu soru bu mağazaya yöneltilmemiş."
            )

        # =====================================================
        # ONE ANSWER PER STORE / QUESTION
        # =====================================================

        if ProductAnswer.objects.filter(
            question=question,
            store=store,
        ).exists():
            raise ValueError(
                "Bu mağaza bu soruya daha önce cevap verdi."
            )

        # =====================================================
        # CREATE
        # =====================================================

        try:
            return ProductAnswer.objects.create(
                question=question,
                store=store,
                user=user,
                text=text,
                is_visible=True,
            )

        except IntegrityError:
            raise ValueError(
                "Bu mağaza bu soruya daha önce cevap verdi."
            )


    # =====================================================
    # CHANGE ANSWER VISIBILITY
    # =====================================================

    @staticmethod
    @transaction.atomic
    def set_answer_visibility(
        *,
        answer,
        is_visible,
    ):
        """
        Cevabın vitrindeki görünürlüğünü değiştirir.

        is_answered olmadığı için ayrıca soru üzerinde
        herhangi bir update yapılmaz.

        Sorgu sonucu otomatik değişir.
        """

        if not isinstance(is_visible, bool):
            raise ValueError(
                "is_visible boolean olmalıdır."
            )

        answer.is_visible = is_visible

        answer.save(
            update_fields=[
                "is_visible",
                "updated_at",
            ],
        )

        return answer

    # =====================================================
    # DELETE QUESTION
    # =====================================================

    @staticmethod
    @transaction.atomic
    def delete_question(
        *,
        question,
        user,
    ):
        """
        Kullanıcı yalnızca kendi sorusunu silebilir.

        Question silindiğinde:

            ProductAnswer
            ProductQuestionUpvote

        kayıtları CASCADE nedeniyle silinir.
        """

        if not user or not user.is_authenticated:
            raise PermissionError(
                "Giriş yapmanız gerekiyor."
            )

        if question.user_id != user.id:
            raise PermissionError(
                "Bu soruyu silme yetkiniz yok."
            )

        question.delete()

    # =====================================================
    # DELETE ANSWER
    # =====================================================

    @staticmethod
    @transaction.atomic
    def delete_answer(
        *,
        answer,
        user,
    ):
        """
        Kullanıcı yalnızca kendi verdiği cevabı silebilir.

        Cevap silindikten sonra ayrıca soru güncellenmez.

        Çünkü Product Detail sorgusu gerçek cevap kayıtlarına
        bakar.

        Son görünür cevap silindiyse soru otomatik olarak
        Product Detail listesinden çıkar.
        """

        if not user or not user.is_authenticated:
            raise PermissionError(
                "Giriş yapmanız gerekiyor."
            )

        if answer.user_id != user.id:
            raise PermissionError(
                "Bu cevabı silme yetkiniz yok."
            )

        answer.delete()

    # =====================================================
    # UPVOTE
    # =====================================================

    @staticmethod
    @transaction.atomic
    def upvote_question(
        *,
        question,
        user,
    ):
        """
        Bir soruya upvote verir.

        Buradaki kritik güvenlik kontrolü:

            question.is_visible == True
            +
            en az bir görünür cevap

        olmalıdır.

        Böylece kullanıcı eski/manuel question_id göndererek
        cevapsız veya artık vitrinde olmayan bir soruya oy
        veremez.
        """

        if not user or not user.is_authenticated:
            raise PermissionError(
                "Giriş yapmanız gerekiyor."
            )

        # -------------------------------------------------
        # QUESTION VISIBILITY
        # -------------------------------------------------

        if not question.is_visible:
            raise ValueError(
                "Bu soru artık görünür değil."
            )

        # -------------------------------------------------
        # VISIBLE ANSWER
        # -------------------------------------------------

        has_visible_answer = (
            ProductAnswer.objects
            .filter(
                question_id=question.id,
                is_visible=True,
            )
            .exists()
        )

        if not has_visible_answer:
            raise ValueError(
                "Cevaplanmamış bir soruya oy veremezsiniz."
            )

        # -------------------------------------------------
        # OWN QUESTION
        # -------------------------------------------------

        if question.user_id == user.id:
            raise ValueError(
                "Kendi sorunuzu faydalı bulamazsınız."
            )

        # -------------------------------------------------
        # CREATE UNIQUE VOTE
        # -------------------------------------------------

        try:
            ProductQuestionUpvote.objects.create(
                question=question,
                user=user,
            )

        except IntegrityError:
            raise ValueError(
                "Bu soruya zaten oy verdiniz."
            )

        # -------------------------------------------------
        # ATOMIC COUNTER INCREMENT
        # -------------------------------------------------

        ProductQuestion.objects.filter(
            pk=question.id,
        ).update(
            upvotes=F("upvotes") + 1,
        )

        question.refresh_from_db(
            fields=["upvotes"],
        )

        return question.upvotes