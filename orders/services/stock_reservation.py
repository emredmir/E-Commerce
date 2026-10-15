from collections import defaultdict
from datetime import timedelta
from typing import Iterable

from django.db import IntegrityError, transaction
from django.db.models import Sum
from django.utils import timezone

from products.models import (
    ProductStatus,
    StoreProduct,
    StoreProductStatus,
)

from ..exceptions import (
    InsufficientStockError,
    InvalidOrderStateError,
    OrderNotFoundError,
    ProductUnavailableError,
    ReservationError,
    ReservationExpiredError,
)

from ..models import (
    Order,
    OrderItem,
    OrderStatus,
    ReservationStatus,
    ReturnRequest,
    StockReservation,
    SubOrderItemCancellationUnit,
)


class StockReservationService:
    """
    Stock reservation domain business logic.

    Reservation lifecycle:

        ACTIVE
          ├──> RELEASED
          ├──> EXPIRED
          └──> CONSUMED
           │
           └──> RELEASED   # sipariş iptali

    Temel kurallar:

        1. Reservation oluştururken fiziksel stock düşmez.
        2. ACTIVE + süresi dolmamış reservation'lar available stock'u azaltır.
        3. RELEASED / EXPIRED reservation'lar stock'u bloke etmez.
        4. CONSUMED reservation fiziksel stock tüketimini temsil eder.
        5. Reservation oluştururken StoreProduct lock edilir.
        6. Concurrent reservation işlemleri deterministic lock sırasıyla
           serialize edilir.
        7. Payment provider bu servisin sorumluluğunda değildir.
        8. Payment başarıyla doğrulandıktan sonra consume_order()
           payment/orchestration katmanı tarafından çağrılır.
        9. Order state transition bu servisin sorumluluğunda değildir.

    Mutation lock order:

        Order
          ↓
        StoreProduct ASC
          ↓
        StockReservation

    Bütün stock mutation operasyonlarında mümkün olduğunca
    bu lock sırası korunmalıdır.
    """

    # ======================================================================
    # CONFIGURATION
    # ======================================================================

    RESERVATION_DURATION = timedelta(minutes=15)

    RESERVABLE_ORDER_STATES = {
        OrderStatus.PENDING_PAYMENT,
    }

    # Payment verification başarılı olduktan sonra
    # orchestration katmanının consume_order() çağırdığı state.
    #
    # Eğer payment verification sonrasında Order hâlâ
    # PENDING_PAYMENT durumundaysa bu set doğrudur.
    #
    # Eğer payment verification önce Order'ı PAID yapıyorsa:
    #
    #     CONSUMABLE_ORDER_STATES = {
    #         OrderStatus.PAID,
    #     }
    #
    # şeklinde değiştirilmelidir.
    CONSUMABLE_ORDER_STATES = {
        OrderStatus.PENDING_PAYMENT,
    }

    # ======================================================================
    # RESERVE ORDER
    # ======================================================================

    @classmethod
    @transaction.atomic
    def reserve_order(
        cls,
        *,
        order: Order,
        now=None,
    ) -> list[StockReservation]:
        """
        Order içerisindeki tüm OrderItem'lar için reservation oluşturur.

        Atomiktir.

        Herhangi bir ürün için yeterli available stock yoksa
        bütün transaction rollback olur.

        Idempotent davranır:

            OrderItem için hâlihazırda ACTIVE ve süresi dolmamış
            reservation varsa yeni reservation oluşturmaz.
        """

        now = now or timezone.now()

        locked_order = cls._lock_order(
            order_id=order.pk,
        )

        if locked_order.status not in cls.RESERVABLE_ORDER_STATES:
            raise InvalidOrderStateError(
                "Reservation yalnızca ödeme bekleyen "
                "sipariş için oluşturulabilir."
            )

        order_items = cls._get_order_items(
            order=locked_order,
        )

        if not order_items:
            raise ReservationError(
                "Reservation oluşturmak için "
                "siparişte ürün bulunmuyor."
            )

        return cls._reserve_items(
            order_items=order_items,
            now=now,
        )

    # ======================================================================
    # RESERVE SINGLE ITEM
    # ======================================================================

    @classmethod
    @transaction.atomic
    def reserve(
        cls,
        *,
        order_item: OrderItem,
        now=None,
    ) -> StockReservation:
        """
        Tek bir OrderItem için reservation oluşturur.

        Normal checkout flow'da reserve_order() tercih edilmelidir.
        """

        now = now or timezone.now()

        fresh_item = (
            OrderItem.objects
            .select_related(
                "sub_order__order",
                "store_product",
                "store_product__store",
                "store_product__variant",
                "store_product__variant__product",
            )
            .filter(
                pk=order_item.pk,
            )
            .first()
        )

        if not fresh_item:
            raise ReservationError(
                "Reservation için OrderItem bulunamadı."
            )

        locked_order = cls._lock_order(
            order_id=fresh_item.sub_order.order_id,
        )

        if locked_order.status not in cls.RESERVABLE_ORDER_STATES:
            raise InvalidOrderStateError(
                "Reservation yalnızca ödeme bekleyen "
                "sipariş için oluşturulabilir."
            )

        if fresh_item.sub_order.order_id != locked_order.pk:
            raise ReservationError(
                "OrderItem ilgili siparişe ait değil."
            )

        reservations = cls._reserve_items(
            order_items=[fresh_item],
            now=now,
        )

        return reservations[0]

    # ======================================================================
    # INTERNAL RESERVATION
    # ======================================================================

    @classmethod
    def _reserve_items(
        cls,
        *,
        order_items: Iterable[OrderItem],
        now,
    ) -> list[StockReservation]:
        """
        Reservation oluşturmanın ortak implementation'ı.

        Lock sırası:

            StoreProduct ASC
                ↓
            StockReservation

        Aynı StoreProduct için birden fazla OrderItem varsa
        mevcut ACTIVE reservation'lar aggregate edilerek
        available stock hesaplanır.
        """

        order_items = sorted(
            list(order_items),
            key=lambda item: (
                item.store_product_id,
                item.pk,
            ),
        )

        store_products = cls._lock_store_products(
            order_items=order_items,
        )

        store_product_ids = sorted(
            store_products.keys(),
        )

        active_reservations = cls._lock_active_reservations(
            store_product_ids=store_product_ids,
        )

        cls._expire_locked_reservations(
            reservations=active_reservations,
            now=now,
        )

        active_by_item: dict[
            int,
            StockReservation,
        ] = {}

        active_by_product: dict[
            int,
            list[StockReservation],
        ] = defaultdict(list)

        for reservation in active_reservations:

            if reservation.status != ReservationStatus.ACTIVE:
                continue

            if reservation.expires_at <= now:
                continue

            # Aynı OrderItem için yalnızca bir aktif reservation
            # bulunmasına izin verilir.
            if reservation.order_item_id in active_by_item:
                raise ReservationError(
                    f"OrderItem #{reservation.order_item_id} "
                    "için birden fazla aktif reservation bulundu. "
                    "Stok işlemi güvenli şekilde tamamlanamadı."
                )

            active_by_item[
                reservation.order_item_id
            ] = reservation

            store_product_id = (
                reservation.order_item.store_product_id
            )

            active_by_product[
                store_product_id
            ].append(
                reservation
            )

        result: list[StockReservation] = []

        for order_item in order_items:

            store_product = store_products.get(
                order_item.store_product_id,
            )

            if not store_product:
                raise ProductUnavailableError(
                    "Sipariş ürününün StoreProduct "
                    "kaydı bulunamadı."
                )

            # --------------------------------------------------------------
            # Existing reservation
            # --------------------------------------------------------------

            existing_reservation = active_by_item.get(
                order_item.pk,
            )

            if existing_reservation:

                if (
                    existing_reservation.quantity
                    != order_item.quantity
                ):
                    raise ReservationError(
                        "Mevcut reservation miktarı "
                        "OrderItem miktarıyla uyuşmuyor."
                    )

                result.append(
                    existing_reservation
                )

                continue

            # --------------------------------------------------------------
            # Product validation
            # --------------------------------------------------------------

            cls._validate_store_product_for_reservation(
                store_product=store_product,
            )

            # --------------------------------------------------------------
            # Quantity validation
            # --------------------------------------------------------------

            quantity = order_item.quantity

            if quantity < 1:
                raise ReservationError(
                    "Reservation miktarı en az 1 olmalıdır."
                )

            # --------------------------------------------------------------
            # Current active reservations
            # --------------------------------------------------------------

            reserved_quantity = sum(
                reservation.quantity
                for reservation
                in active_by_product[
                    store_product.pk
                ]
            )

            # --------------------------------------------------------------
            # Available stock
            # --------------------------------------------------------------

            available_stock = max(
                store_product.stock
                - reserved_quantity,
                0,
            )

            if quantity > available_stock:

                product_name = (
                    store_product
                    .variant
                    .product
                    .name
                )

                raise InsufficientStockError(
                    f"'{product_name}' için yeterli "
                    f"rezerve edilebilir stok bulunmuyor. "
                    f"Mevcut: {available_stock}, "
                    f"İstenen: {quantity}."
                )

            # --------------------------------------------------------------
            # Expiration
            # --------------------------------------------------------------

            expires_at = (
                now
                + cls.RESERVATION_DURATION
            )

            # --------------------------------------------------------------
            # Create reservation
            # --------------------------------------------------------------

            try:

                reservation = (
                    StockReservation.objects.create(
                        order_item=order_item,
                        quantity=quantity,
                        status=ReservationStatus.ACTIVE,
                        expires_at=expires_at,
                    )
                )

            except IntegrityError as exc:

                raise ReservationError(
                    "Reservation oluşturulurken "
                    "veritabanı constraint'i ihlal edildi."
                ) from exc

            result.append(
                reservation
            )

            active_by_item[
                order_item.pk
            ] = reservation

            active_by_product[
                store_product.pk
            ].append(
                reservation
            )

        return result

    # ======================================================================
    # CONSUME ORDER
    # ======================================================================

    @classmethod
    def consume_order(
        cls,
        *,
        order: Order,
        now=None,
    ) -> list[StockReservation]:
        """
        Payment verification başarılı olduktan sonra
        Order reservation'larını consume eder.

        ACTIVE:
            -> CONSUMED
            -> fiziksel stock düşer
            -> sold_count artar

        ACTIVE + expired:
            -> EXPIRED
            -> stock tüketilmez
            -> ReservationExpiredError

        CONSUMED:
            -> tekrar stock düşürülmez

        Bu method:

            - payment başlatmaz
            - payment doğrulamaz
            - Order state transition yapmaz

        Payment/orchestration katmanı tarafından
        payment başarıyla doğrulandıktan sonra çağrılmalıdır.
        """

        now = now or timezone.now()

        result = None
        expired_message = None

        with transaction.atomic():
            result, expired_message = cls._consume_order_locked(
                order=order,
                now=now,
            )

        # Transaction başarılı şekilde commit edildikten sonra
        # exception dışarıda fırlatılır.
        #
        # Böylece ACTIVE -> EXPIRED değişikliği DB'de korunur.
        if expired_message:
            raise ReservationExpiredError(
                expired_message
            )

        return result

    @classmethod
    def _consume_order_locked(
        cls,
        *,
        order: Order,
        now,
    ) -> tuple[list[StockReservation], str | None]:
        """
        consume_order() transaction'ının iç implementation'ı.

        Bu method transaction dışında public API olarak kullanılmamalıdır.
        """

        locked_order = cls._lock_order(
            order_id=order.pk,
        )

        if locked_order.status not in cls.CONSUMABLE_ORDER_STATES:
            raise InvalidOrderStateError(
                "Bu sipariş durumunda stok tüketilemez."
            )

        order_items = cls._get_order_items(
            order=locked_order,
        )

        if not order_items:
            raise ReservationError(
                "Reservation consume için "
                "siparişte ürün bulunmuyor."
            )

        # ------------------------------------------------------------------
        # StoreProduct lock
        # ------------------------------------------------------------------

        store_products = cls._lock_store_products(
            order_items=order_items,
        )

        # ------------------------------------------------------------------
        # Reservation lock
        # ------------------------------------------------------------------

        order_item_ids = [
            item.pk
            for item in order_items
        ]

        reservations = list(
            StockReservation.objects
            .select_for_update()
            .filter(
                order_item_id__in=order_item_ids,
            )
            .order_by(
                "order_item_id",
                "-created_at",
                "-id",
            )
        )

        # ------------------------------------------------------------------
        # Latest reservation per OrderItem
        # ------------------------------------------------------------------

        latest_by_item: dict[
            int,
            StockReservation,
        ] = {}

        for reservation in reservations:

            if reservation.order_item_id in latest_by_item:
                continue

            latest_by_item[
                reservation.order_item_id
            ] = reservation

        to_consume: list[
            StockReservation
        ] = []

        expired_message = None

        # ------------------------------------------------------------------
        # Validate reservation states
        # ------------------------------------------------------------------

        for order_item in order_items:

            reservation = latest_by_item.get(
                order_item.pk,
            )

            if not reservation:
                raise ReservationError(
                    f"OrderItem #{order_item.pk} "
                    "için reservation bulunamadı."
                )

             # Reservation miktarı ile sipariş kalemi tutarlılığı.
            if reservation.quantity != order_item.quantity:
                raise ReservationError(
                    f"OrderItem #{order_item.pk} ile "
                    "reservation miktarı eşleşmiyor."
                )

            # --------------------------------------------------------------
            # Already consumed
            # --------------------------------------------------------------

            if (
                reservation.status
                == ReservationStatus.CONSUMED
            ):
                continue

            # --------------------------------------------------------------
            # Active
            # --------------------------------------------------------------

            if (
                reservation.status
                == ReservationStatus.ACTIVE
            ):

                if reservation.expires_at <= now:

                    reservation.status = (
                        ReservationStatus.EXPIRED
                    )

                    reservation.save(
                        update_fields=[
                            "status",
                            "updated_at",
                        ]
                    )

                    if expired_message is None:
                        expired_message = (
                            f"OrderItem #{order_item.pk} "
                            "reservation süresi dolmuş."
                        )

                    continue

                # ----------------------------------------------------------
                # Kritik:
                # Expire olmayan ACTIVE reservation consume listesine girer.
                # ----------------------------------------------------------

                to_consume.append(
                    reservation
                )

                continue

            # --------------------------------------------------------------
            # Expired
            # --------------------------------------------------------------

            if (
                reservation.status
                == ReservationStatus.EXPIRED
            ):
                raise ReservationExpiredError(
                    f"OrderItem #{order_item.pk} "
                    "reservation süresi dolmuş."
                )

            # --------------------------------------------------------------
            # Released
            # --------------------------------------------------------------

            if (
                reservation.status
                == ReservationStatus.RELEASED
            ):
                raise ReservationError(
                    f"OrderItem #{order_item.pk} "
                    "reservation zaten serbest bırakılmış."
                )

            raise ReservationError(
                f"OrderItem #{order_item.pk} "
                "için geçersiz reservation durumu."
            )

        # ------------------------------------------------------------------
        # Expired reservation varsa hiçbir reservation consume etme.
        #
        # Burada transaction dışarı çıkacak ve commit olacak.
        # Böylece EXPIRED state DB'de korunacak.
        # ------------------------------------------------------------------

        if expired_message:
            return [], expired_message

        # ------------------------------------------------------------------
        # Nothing to consume
        # ------------------------------------------------------------------

        if not to_consume:
            return [], None

        # ------------------------------------------------------------------
        # Aggregate quantity
        # ------------------------------------------------------------------

        quantity_by_store_product: dict[
            int,
            int,
        ] = defaultdict(int)

        for reservation in to_consume:

            quantity_by_store_product[
                reservation
                .order_item
                .store_product_id
            ] += reservation.quantity

        # ------------------------------------------------------------------
        # Physical stock validation
        # ------------------------------------------------------------------

        for (
            store_product_id,
            quantity,
        ) in quantity_by_store_product.items():

            store_product = store_products.get(
                store_product_id,
            )

            if not store_product:
                raise ProductUnavailableError(
                    "Reservation'a bağlı StoreProduct "
                    "bulunamadı."
                )

            if store_product.stock < quantity:

                product_name = (
                    store_product
                    .variant
                    .product
                    .name
                )

                raise InsufficientStockError(
                    f"'{product_name}' için fiziksel stok "
                    f"yeterli değil. "
                    f"Mevcut: {store_product.stock}, "
                    f"Gerekli: {quantity}."
                )

        # ------------------------------------------------------------------
        # Physical stock finalization
        # ------------------------------------------------------------------

        for (
            store_product_id,
            quantity,
        ) in quantity_by_store_product.items():

            store_product = store_products[
                store_product_id
            ]

            store_product.stock -= quantity
            store_product.sold_count += quantity

            store_product.save(
                update_fields=[
                    "stock",
                    "sold_count",
                    "status",
                    "updated_at",
                ]
            )

        # ------------------------------------------------------------------
        # Reservation finalization
        # ------------------------------------------------------------------

        for reservation in to_consume:

            reservation.status = (
                ReservationStatus.CONSUMED
            )

            reservation.save(
                update_fields=[
                    "status",
                    "updated_at",
                ]
            )

        return to_consume, None

    # ======================================================================
    # RELEASE ORDER
    # ======================================================================

    @classmethod
    @transaction.atomic
    def release_order(
        cls,
        *,
        order: Order,
        now=None,
    ) -> list[StockReservation]:
        """
        Order'a ait ACTIVE reservation'ları serbest bırakır.

        ACTIVE:
            -> RELEASED

        Süresi geçmiş ACTIVE:
            -> EXPIRED

        Fiziksel stock değiştirilmez.

        Idempotent'tır.
        """

        now = now or timezone.now()

        locked_order = cls._lock_order(
            order_id=order.pk,
        )

        order_items = cls._get_order_items(
            order=locked_order,
        )

        if not order_items:
            return []

        cls._lock_store_products(
            order_items=order_items,
        )

        order_item_ids = [
            item.pk
            for item in order_items
        ]

        reservations = list(
            StockReservation.objects
            .select_for_update()
            .filter(
                order_item_id__in=order_item_ids,
                status=ReservationStatus.ACTIVE,
            )
            .order_by(
                "order_item__store_product_id",
                "order_item_id",
                "id",
            )
        )

        for reservation in reservations:

            if reservation.expires_at <= now:

                reservation.status = (
                    ReservationStatus.EXPIRED
                )

            else:

                reservation.status = (
                    ReservationStatus.RELEASED
                )

            reservation.save(
                update_fields=[
                    "status",
                    "updated_at",
                ]
            )

        return reservations



    @classmethod
    @transaction.atomic
    def restore_cancelled_quantity(
        cls,
        *,
        order_item_id: int,
        quantity: int,
    ) -> StoreProduct:
        """
        İptal edilen miktar kadar fiziksel stoğu geri yükler.

        İptal edilen miktarın doğrulanması ve aynı iptal
        işleminin tekrarlanmasının engellenmesi CancellationService
        tarafından, aynı transaction içerisinde sağlanmalıdır.

        Lock sırası:
            Order -> StoreProduct -> OrderItem
        """

        if (
            isinstance(quantity, bool)
            or not isinstance(quantity, int)
            or quantity < 1
        ):
            raise ReservationError(
                "Stok iadesi için adet pozitif bir tam sayı olmalıdır."
            )

        # Ön bilgi sorgusu; henüz satır kilitlenmiyor.
        item_reference = (
            OrderItem.objects
            .select_related("sub_order")
            .filter(pk=order_item_id)
            .first()
        )

        if item_reference is None:
            raise ReservationError(
                "Stok iadesi için sipariş kalemi bulunamadı."
            )

        # 1. Order lock
        locked_order = cls._lock_order(
            order_id=item_reference.sub_order.order_id,
        )

        # 2. StoreProduct lock
        try:
            store_products = cls._lock_store_products(
                order_items=[item_reference],
            )
        except ProductUnavailableError as exc:
            raise ReservationError(
                "Sipariş kalemine bağlı mağaza ürünü bulunamadı."
            ) from exc

        # 3. OrderItem lock
        try:
            order_item = (
                OrderItem.objects
                .select_for_update()
                .get(
                    pk=order_item_id,
                    sub_order__order_id=locked_order.pk,
                )
            )
        except OrderItem.DoesNotExist as exc:
            raise ReservationError(
                "Sipariş kalemi ilgili siparişte bulunamadı."
            ) from exc

        # Kilit alınmadan önce okunan ürün ilişkisi değişmişse
        # yanlış StoreProduct üzerinde işlem yapma.
        if (
            order_item.store_product_id
            != item_reference.store_product_id
        ):
            raise ReservationError(
                "Sipariş kalemine bağlı mağaza ürünü değişmiş."
            )

        store_product = store_products.get(
            order_item.store_product_id,
        )

        if store_product is None:
            raise ReservationError(
                "Mağaza ürünü kilitlenemedi."
            )

        if store_product.sold_count < quantity:
            raise ReservationError(
                "Ürünün satılmış adet bilgisi "
                "stok iadesi için geçersiz."
            )

        store_product.stock += quantity
        store_product.sold_count -= quantity

        store_product.save(
            update_fields=[
                "stock",
                "sold_count",
                "status",
                "updated_at",
            ]
        )

        return store_product



    @classmethod
    def restore_consumed_suborder(
        cls,
        *,
        suborder,
    ) -> list[StockReservation]:
        """
        Alt siparişin CONSUMED reservation'ını kapatıp yalnızca henüz
        stoğa geri eklenmemiş birimleri geri yükler.

        CancellationService iptal edilen birimleri, ReturnService ise
        stock_restored_at işaretli cayma iadelerini daha önce stoğa
        eklemiş olabilir. Bu birimler ikinci kez eklenmez.

        Lock sırası:
            Order -> StoreProduct ASC -> StockReservation

        Aynı ürün biriminin birden fazla stok iadesi kaydında görülmesi
        halinde otomatik hareket yapılmaz; veri manuel uzlaştırılmalıdır.
        """

        with transaction.atomic():
            locked_order = cls._lock_order(
                order_id=suborder.order_id,
            )

            order_items = list(
                OrderItem.objects
                .select_related(
                    "sub_order",
                    "store_product",
                )
                .filter(
                    sub_order_id=suborder.pk,
                    sub_order__order_id=locked_order.pk,
                )
                .order_by(
                    "store_product_id",
                    "id",
                )
            )

            if not order_items:
                raise ReservationError(
                    "İptal edilen siparişte ürün bulunamadı."
                )

            store_products = cls._lock_store_products(
                order_items=order_items,
            )

            order_items_by_id = {
                item.pk: item for item in order_items
            }
            order_item_ids = list(order_items_by_id)

            # CancellationService'in stoğu geri yüklediği satın alınmış
            # birimler. Duplicate rows veri tutarsızlığı sayılır.
            restored_units_by_item = defaultdict(set)
            cancellation_units = list(
                SubOrderItemCancellationUnit.objects
                .filter(order_item_id__in=order_item_ids)
                .order_by("order_item_id", "unit_number", "pk")
                .values_list("order_item_id", "unit_number")
            )

            for order_item_id, unit_number in cancellation_units:
                order_item = order_items_by_id[order_item_id]
                if not 1 <= unit_number <= order_item.quantity:
                    raise ReservationError(
                        "İptal stok kayıtlarında geçersiz ürün birimi bulundu."
                    )
                if unit_number in restored_units_by_item[order_item_id]:
                    raise ReservationError(
                        "Aynı ürün birimi birden fazla iptal stok kaydıyla "
                        "ilişkilendirilmiş; manuel kontrol gerekli."
                    )
                restored_units_by_item[order_item_id].add(unit_number)

            # Yalnızca gerçekten stok geri yüklenmiş ReturnRequest'ler
            # hesaba katılır. Diğer iade aşamaları fiziksel stok anlamına gelmez.
            returned_units = list(
                ReturnRequest.objects
                .filter(
                    order_item_id__in=order_item_ids,
                    stock_restored_at__isnull=False,
                )
                .order_by("order_item_id", "unit_number", "pk")
                .values_list("order_item_id", "unit_number")
            )

            for order_item_id, unit_number in returned_units:
                order_item = order_items_by_id[order_item_id]
                if not 1 <= unit_number <= order_item.quantity:
                    raise ReservationError(
                        "İade stok kayıtlarında geçersiz ürün birimi bulundu."
                    )
                if unit_number in restored_units_by_item[order_item_id]:
                    raise ReservationError(
                        "Aynı ürün birimi birden fazla stok geri yükleme "
                        "kaydında bulunuyor; manuel kontrol gerekli."
                    )
                restored_units_by_item[order_item_id].add(unit_number)

            reservations = list(
                StockReservation.objects
                .select_for_update()
                .select_related("order_item")
                .filter(order_item_id__in=order_item_ids)
                .order_by("order_item_id", "-created_at", "-id")
            )

            latest_by_item = {}
            for reservation in reservations:
                latest_by_item.setdefault(
                    reservation.order_item_id,
                    reservation,
                )

            to_release = []
            quantity_by_reservation_id = {}
            quantity_by_store_product = defaultdict(int)

            for order_item in order_items:
                reservation = latest_by_item.get(order_item.pk)
                if reservation is None:
                    raise ReservationError(
                        f"OrderItem #{order_item.pk} için reservation bulunamadı."
                    )

                if reservation.status == ReservationStatus.RELEASED:
                    continue

                if reservation.status != ReservationStatus.CONSUMED:
                    raise ReservationError(
                        f"OrderItem #{order_item.pk} için reservation stok "
                        "iadesine uygun durumda değil."
                    )

                if reservation.quantity != order_item.quantity:
                    raise ReservationError(
                        f"OrderItem #{order_item.pk} ile reservation miktarı "
                        "eşleşmiyor."
                    )

                already_restored = len(
                    restored_units_by_item.get(order_item.pk, set())
                )
                quantity_to_restore = reservation.quantity - already_restored

                if quantity_to_restore < 0:
                    raise ReservationError(
                        f"OrderItem #{order_item.pk} için stoğa geri yüklenen "
                        "birim sayısı reservation miktarını aşıyor."
                    )

                to_release.append(reservation)
                quantity_by_reservation_id[reservation.pk] = quantity_to_restore

                if quantity_to_restore:
                    quantity_by_store_product[
                        order_item.store_product_id
                    ] += quantity_to_restore

            # Bütün sold_count kontrolleri mutasyondan önce yapılır.
            for store_product_id, quantity in quantity_by_store_product.items():
                store_product = store_products[store_product_id]
                if store_product.sold_count < quantity:
                    raise ReservationError(
                        "Ürünün satılmış adet bilgisi stok iadesi için geçersiz."
                    )

            for store_product_id, quantity in quantity_by_store_product.items():
                store_product = store_products[store_product_id]
                store_product.stock += quantity
                store_product.sold_count -= quantity
                store_product.save(
                    update_fields=[
                        "stock",
                        "sold_count",
                        "status",
                        "updated_at",
                    ]
                )

            for reservation in to_release:
                reservation.status = ReservationStatus.RELEASED
                reservation.save(
                    update_fields=["status", "updated_at"]
                )

            return to_release


    @classmethod
    @transaction.atomic
    def decrement_sold_count_for_return(
        cls,
        *,
        order: Order,
        order_item_id: int,
        quantity: int = 1,
    ) -> StoreProduct:
        """
        Kabul edilmiş fakat yeniden satılabilir stoğa dönmeyecek bir iade için
        yalnızca sold_count değerini azaltır; stock değerine dokunmaz.

        ReturnService, ReturnRequest.sold_count_adjusted_at alanıyla bu işlemin
        aynı iade için yalnızca bir kez uygulanmasını sağlamalıdır.

        Kilit sırası: Order -> StoreProduct -> OrderItem.
        """
        if (
            isinstance(quantity, bool)
            or not isinstance(quantity, int)
            or quantity <= 0
        ):
            raise ReservationError(
                "Satış sayacı düzeltme miktarı pozitif bir tam sayı olmalıdır."
            )

        locked_order = cls._lock_order(order_id=order.pk)

        item_reference = (
            OrderItem.objects
            .filter(
                pk=order_item_id,
                sub_order__order_id=locked_order.pk,
            )
            .first()
        )
        if item_reference is None:
            raise ReservationError(
                "Satış sayacı düzeltmesi için sipariş ürünü bulunamadı."
            )

        try:
            store_products = cls._lock_store_products(
                order_items=[item_reference],
            )
        except ProductUnavailableError as exc:
            raise ReservationError(
                "Satış sayacı düzeltmesi için mağaza ürünü bulunamadı."
            ) from exc

        try:
            order_item = (
                OrderItem.objects
                .select_for_update()
                .get(
                    pk=order_item_id,
                    sub_order__order_id=locked_order.pk,
                )
            )
        except OrderItem.DoesNotExist as exc:
            raise ReservationError(
                "Satış sayacı düzeltmesi ilgili siparişte bulunamadı."
            ) from exc

        if order_item.store_product_id != item_reference.store_product_id:
            raise ReservationError(
                "Sipariş kalemine bağlı mağaza ürünü değişmiş."
            )

        store_product = store_products.get(order_item.store_product_id)
        if store_product is None:
            raise ReservationError(
                "Mağaza ürünü kilitlenemedi."
            )

        if store_product.sold_count < quantity:
            raise ReservationError(
                "Ürünün satılmış adet bilgisi iade düzeltmesi için geçersiz."
            )

        # Deliberately do not increase stock: this return is not sellable stock.
        store_product.sold_count -= quantity
        store_product.save(
            update_fields=["sold_count", "updated_at"]
        )
        return store_product


    @classmethod
    @transaction.atomic
    def restore_returned_item(
        cls,
        *,
        order: Order,
        order_item_id: int,
        quantity: int = 1,
    ) -> StoreProduct:
        """
        Satıcı tarafından kabul edilen, yeniden satılabilir
        ürün birimini stoğa geri ekler.

        İade uygunluğu ve aynı iadenin tekrar işlenmemesi
        ReturnService tarafından güvence altına alınmalıdır.

        Lock sırası:
            Order -> StoreProduct -> OrderItem
        """

        if (
            isinstance(quantity, bool)
            or not isinstance(quantity, int)
            or quantity <= 0
        ):
            raise ReservationError(
                "Stok iade miktarı pozitif bir tam sayı olmalıdır."
            )

        # 1. Order lock
        locked_order = cls._lock_order(order_id=order.pk)

        # Kilit almadan önce yalnızca StoreProduct kimliğini
        # öğrenmek için kullanılan referans sorgusu.
        item_reference = (
            OrderItem.objects
            .filter(
                pk=order_item_id,
                sub_order__order_id=locked_order.pk,
            )
            .first()
        )

        if item_reference is None:
            raise ReservationError(
                "Stok iadesi için sipariş ürünü bulunamadı."
            )

        # 2. StoreProduct lock
        try:
            store_products = cls._lock_store_products(
                order_items=[item_reference],
            )
        except ProductUnavailableError as exc:
            raise ReservationError(
                "Stok iadesi için mağaza ürünü bulunamadı."
            ) from exc

        # 3. OrderItem lock
        try:
            order_item = (
                OrderItem.objects
                .select_for_update()
                .get(
                    pk=order_item_id,
                    sub_order__order_id=locked_order.pk,
                )
            )
        except OrderItem.DoesNotExist as exc:
            raise ReservationError(
                "Stok iadesi ilgili siparişte bulunamadı."
            ) from exc

        # Ürün ilişkisi değiştiyse yanlış mağaza ürününün
        # stoğuna müdahale etme.
        if (
            order_item.store_product_id
            != item_reference.store_product_id
        ):
            raise ReservationError(
                "Sipariş kalemine bağlı mağaza ürünü değişmiş."
            )

        store_product = store_products.get(
            order_item.store_product_id,
        )

        if store_product is None:
            raise ReservationError(
                "Mağaza ürünü kilitlenemedi."
            )

        if store_product.sold_count < quantity:
            raise ReservationError(
                "Ürünün satılmış adet bilgisi "
                "stok iadesi için geçersiz."
            )

        store_product.stock += quantity
        store_product.sold_count -= quantity

        store_product.save(
            update_fields=[
                "stock",
                "sold_count",
                "status",
                "updated_at",
            ]
        )

        return store_product

    # ======================================================================
    # RELEASE SINGLE RESERVATION
    # ======================================================================

    @classmethod
    @transaction.atomic
    def release(
        cls,
        *,
        reservation: StockReservation,
        now=None,
    ) -> StockReservation:
        """
        Tek reservation release eder.

        ACTIVE:
            -> RELEASED

        Süresi dolmuş ACTIVE:
            -> EXPIRED

        Terminal state:
            -> değişmeden döner.
        """

        now = now or timezone.now()

        reservation_info = (
            StockReservation.objects
            .select_related(
                "order_item",
                "order_item__sub_order",
            )
            .filter(
                pk=reservation.pk,
            )
            .first()
        )

        if not reservation_info:
            raise ReservationError(
                "Reservation bulunamadı."
            )

        # Lock order:
        # Order -> StoreProduct -> StockReservation

        cls._lock_order(
            order_id=reservation_info.order_item.sub_order.order_id,
        )

        StoreProduct.objects.select_for_update().get(
            pk=reservation_info.order_item.store_product_id,
        )

        locked_reservation = (
            StockReservation.objects
            .select_for_update()
            .get(
                pk=reservation.pk,
            )
        )

        if (
            locked_reservation.status
            != ReservationStatus.ACTIVE
        ):
            return locked_reservation

        if locked_reservation.expires_at <= now:

            locked_reservation.status = (
                ReservationStatus.EXPIRED
            )

        else:

            locked_reservation.status = (
                ReservationStatus.RELEASED
            )

        locked_reservation.save(
            update_fields=[
                "status",
                "updated_at",
            ]
        )

        return locked_reservation

    # ======================================================================
    # EXPIRE SINGLE RESERVATION
    # ======================================================================

    @classmethod
    @transaction.atomic
    def expire(
        cls,
        *,
        reservation: StockReservation,
        now=None,
    ) -> StockReservation:
        """
        Tek reservation'ın süresi dolmuşsa:

            ACTIVE -> EXPIRED

        Henüz süresi dolmamışsa exception verir.

        Terminal state'lerde mevcut state döndürülür.
        """

        now = now or timezone.now()

        reservation_info = (
            StockReservation.objects
            .select_related(
                "order_item",
                "order_item__sub_order",
            )
            .filter(
                pk=reservation.pk,
            )
            .first()
        )

        if not reservation_info:
            raise ReservationError(
                "Reservation bulunamadı."
            )

        cls._lock_order(
            order_id=reservation_info.order_item.sub_order.order_id,
        )

        StoreProduct.objects.select_for_update().get(
            pk=reservation_info.order_item.store_product_id,
        )

        locked_reservation = (
            StockReservation.objects
            .select_for_update()
            .get(
                pk=reservation.pk,
            )
        )

        if (
            locked_reservation.status
            != ReservationStatus.ACTIVE
        ):
            return locked_reservation

        if locked_reservation.expires_at > now:
            raise ReservationError(
                "Reservation henüz süresi dolmadı."
            )

        locked_reservation.status = (
            ReservationStatus.EXPIRED
        )

        locked_reservation.save(
            update_fields=[
                "status",
                "updated_at",
            ]
        )

        return locked_reservation

    # ======================================================================
    # EXPIRE DUE RESERVATIONS
    # ======================================================================

    @classmethod
    def expire_due_reservations(
        cls,
        *,
        now=None,
        limit: int = 500,
    ) -> list[StockReservation]:
        """
        Celery / scheduler / management command tarafından çağrılır.

        Süresi dolmuş ACTIVE reservation'ları bulur.

        Her reservation ayrı kısa transaction içerisinde expire edilir.
        """

        now = now or timezone.now()

        reservation_ids = list(
            StockReservation.objects
            .filter(
                status=ReservationStatus.ACTIVE,
                expires_at__lte=now,
            )
            .order_by(
                "order_item__store_product_id",
                "id",
            )
            .values_list(
                "id",
                flat=True,
            )[:limit]
        )

        if not reservation_ids:
            return []

        expired: list[
            StockReservation
        ] = []

        for reservation_id in reservation_ids:

            reservation = (
                StockReservation.objects
                .filter(
                    pk=reservation_id,
                )
                .first()
            )

            if not reservation:
                continue

            try:

                reservation = cls.expire(
                    reservation=reservation,
                    now=now,
                )

            except ReservationError:
                continue

            if (
                reservation.status
                == ReservationStatus.EXPIRED
            ):
                expired.append(
                    reservation
                )

        return expired

    # ======================================================================
    # AVAILABLE STOCK
    # ======================================================================

    @classmethod
    def get_available_stock(
        cls,
        *,
        store_product: StoreProduct,
        now=None,
    ) -> int:
        """
        StoreProduct için rezerve edilebilir stok miktarını döndürür.

        Formula:

            available =
                physical stock
                -
                ACTIVE + unexpired reservations

        Bu method read-only'dir.

        Kritik checkout kararında tek başına kullanılmamalıdır.

        Gerçek reservation kararı reserve_order() içinde
        StoreProduct lock altında verilmelidir.
        """

        now = now or timezone.now()

        fresh_store_product = (
            StoreProduct.objects
            .only(
                "id",
                "stock",
            )
            .get(
                pk=store_product.pk,
            )
        )

        reserved_quantity = (
            StockReservation.objects
            .filter(
                order_item__store_product_id=(
                    fresh_store_product.pk
                ),
                status=ReservationStatus.ACTIVE,
                expires_at__gt=now,
            )
            .aggregate(
                total=Sum("quantity"),
            )
            .get("total")
            or 0
        )

        return max(
            fresh_store_product.stock
            - reserved_quantity,
            0,
        )

    # ======================================================================
    # ORDER LOCK
    # ======================================================================

    @staticmethod
    def _lock_order(
        *,
        order_id: int,
    ) -> Order:
        """
        Order satırını transaction boyunca lock eder.
        """

        try:

            return (
                Order.objects
                .select_for_update()
                .get(
                    pk=order_id,
                )
            )

        except Order.DoesNotExist as exc:

            raise OrderNotFoundError(
                "Sipariş bulunamadı."
            ) from exc

    # ======================================================================
    # ORDER ITEMS
    # ======================================================================

    @staticmethod
    def _get_order_items(
        *,
        order: Order,
    ) -> list[OrderItem]:
        """
        Order'a ait tüm OrderItem'ları deterministic sırada getirir.

        Lock değildir.

        Asıl StoreProduct lock'u
        _lock_store_products() tarafından yapılır.
        """

        return list(
            OrderItem.objects
            .select_related(
                "sub_order",
                "store_product",
                "store_product__store",
                "store_product__variant",
                "store_product__variant__product",
            )
            .filter(
                sub_order__order_id=order.pk,
            )
            .order_by(
                "store_product_id",
                "id",
            )
        )

    # ======================================================================
    # STORE PRODUCT LOCK
    # ======================================================================

    @staticmethod
    def _lock_store_products(
        *,
        order_items: Iterable[OrderItem],
    ) -> dict[int, StoreProduct]:
        """
        Gerekli StoreProduct kayıtlarını lock eder.

        Lock sırası:

            StoreProduct ID ASC
        """

        store_product_ids = sorted(
            {
                item.store_product_id
                for item in order_items
            }
        )

        if not store_product_ids:
            return {}

        store_products = (
            StoreProduct.objects
            .select_for_update()
            .select_related(
                "store",
                "variant",
                "variant__product",
            )
            .filter(
                id__in=store_product_ids,
            )
            .order_by(
                "id",
            )
        )

        result = {
            store_product.pk: store_product
            for store_product in store_products
        }

        if len(result) != len(store_product_ids):
            raise ProductUnavailableError(
                "Siparişteki StoreProduct kayıtlarından "
                "biri bulunamadı."
            )

        return result

    # ======================================================================
    # ACTIVE RESERVATION LOCK
    # ======================================================================

    @staticmethod
    def _lock_active_reservations(
        *,
        store_product_ids: Iterable[int],
    ) -> list[StockReservation]:
        """
        İlgili StoreProduct'lara bağlı ACTIVE reservation'ları lock eder.

        Süresi dolmuş ACTIVE reservation'lar da özellikle alınır.
        """

        store_product_ids = sorted(
            set(store_product_ids)
        )

        if not store_product_ids:
            return []

        return list(
            StockReservation.objects
            .select_for_update()
            .select_related(
                "order_item",
            )
            .filter(
                order_item__store_product_id__in=(
                    store_product_ids
                ),
                status=ReservationStatus.ACTIVE,
            )
            .order_by(
                "order_item__store_product_id",
                "id",
            )
        )

    # ======================================================================
    # PRODUCT VALIDATION
    # ======================================================================

    @staticmethod
    def _validate_store_product_for_reservation(
        *,
        store_product: StoreProduct,
    ) -> None:
        """
        Yeni reservation oluşturulabilmesi için StoreProduct'ın
        hâlâ satın alınabilir durumda olduğunu doğrular.

        Fiziksel stock miktarı burada kontrol edilmez.
        """

        product = (
            store_product
            .variant
            .product
        )

        # ------------------------------------------------------------------
        # StoreProduct
        # ------------------------------------------------------------------

        if (
            store_product.status
            != StoreProductStatus.ACTIVE
        ):
            raise ProductUnavailableError(
                f"'{product.name}' artık aktif olarak satışta değil."
            )

        # ------------------------------------------------------------------
        # Store
        # ------------------------------------------------------------------

        if not store_product.store.is_active:
            raise ProductUnavailableError(
                f"'{product.name}' ürünü bulunan mağaza "
                "artık aktif değil."
            )

        # ------------------------------------------------------------------
        # Variant
        # ------------------------------------------------------------------

        if not store_product.variant.is_active:
            raise ProductUnavailableError(
                f"'{product.name}' varyantı artık aktif değil."
            )

        # ------------------------------------------------------------------
        # Product
        # ------------------------------------------------------------------

        if product.status != ProductStatus.ACTIVE:
            raise ProductUnavailableError(
                f"'{product.name}' artık aktif değil."
            )

    # ======================================================================
    # EXPIRE LOCKED RESERVATIONS
    # ======================================================================

    @staticmethod
    def _expire_locked_reservations(
        *,
        reservations: Iterable[StockReservation],
        now,
    ) -> None:
        """
        Daha önce SELECT FOR UPDATE ile lock edilmiş ACTIVE
        reservation'ların süresi dolmuşsa:

            ACTIVE -> EXPIRED
        """

        for reservation in reservations:

            if reservation.expires_at > now:
                continue

            reservation.status = (
                ReservationStatus.EXPIRED
            )

            reservation.save(
                update_fields=[
                    "status",
                    "updated_at",
                ]
            )
