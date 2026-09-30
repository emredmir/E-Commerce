import hashlib
import hmac
import json
import logging
import uuid

from dataclasses import dataclass
from datetime import timedelta

import iyzipay

from django.conf import settings
from django.core.exceptions import ValidationError as DjangoValidationError
from django.core.validators import validate_email
from django.db import IntegrityError, transaction
from django.utils import timezone

from orders.exceptions import (
    CardStorageConsistencyError,
    CardStorageGatewayError,
    CardStorageOperationInProgressError,
    PaymentValidationError,
    StoredCardNotFoundError,
)
from orders.models import (
    PaymentCustomer,
    StoredCard,
    StoredCardOperation,
    StoredCardOperationStatus,
    StoredCardOperationType,
)


logger = logging.getLogger(__name__)


# ==============================================================================
# DTOs
# ==============================================================================


@dataclass(frozen=True)
class StoredCardCreateData:
    card_holder_name: str
    card_number: str
    expire_month: str
    expire_year: str
    card_alias: str = ""


@dataclass(frozen=True)
class ProviderStoredCardData:
    card_token: str
    card_user_key: str

    card_alias: str
    bin_number: str
    last_four_digits: str

    card_type: str
    card_association: str
    card_family: str

    card_bank_code: int | None
    card_bank_name: str

    expire_month: str
    expire_year: str

    provider_external_id: str = ""


# ==============================================================================
# SERVICE
# ==============================================================================


class CardStorageService:
    """
    iyzico Card Storage işlemlerini yönetir.

    Özellikler:

        - create card
        - list local cards
        - provider sync
        - delete card
        - set default card

    Güvenlik:

        - PAN/CVC saklanmaz.
        - Idempotency desteklenir.
        - Request fingerprint tutulur.
        - Provider/local inconsistency explicit olarak işaretlenir.

    Not:

        Provider HTTP çağrıları DB transaction dışında yapılır.
        PENDING operation hiçbir zaman tekrar provider çağrısı için
        claim edilmez; stale durumda reconciliation gerekir.
    """

    PROVIDER = "iyzico"
    LOCALE = "tr"

    CARD_NOT_FOUND_CODE = "3006"

    STALE_OPERATION_AFTER = timedelta(
        minutes=15,
    )

    # ==========================================================================
    # CREATE
    # ==========================================================================

    @classmethod
    def create_card(
        cls,
        *,
        user,
        card: StoredCardCreateData,
        idempotency_key,
        make_default=False,
    ):
        cls._validate_user(user=user)

        idempotency_key = cls._normalize_idempotency_key(
            idempotency_key,
        )

        card = cls._normalize_card_data(
            card=card,
        )

        request_fingerprint = cls._build_create_fingerprint(
            card=card,
            make_default=make_default,
        )

        operation, created = cls._get_or_create_operation(
            user=user,
            operation_type=StoredCardOperationType.CREATE,
            idempotency_key=idempotency_key,
            request_fingerprint=request_fingerprint,
            make_default=make_default,
        )

        if not created:
            # ------------------------------------------------------------------
            # Already completed
            # ------------------------------------------------------------------

            if operation.status == StoredCardOperationStatus.SUCCESS:
                return cls._get_operation_card(
                    operation=operation,
                )

            # ------------------------------------------------------------------
            # Previous failure
            # ------------------------------------------------------------------

            if operation.status == StoredCardOperationStatus.FAILED:
                raise PaymentValidationError(
                    operation.error_message
                    or "Kart kayıt işlemi başarısız oldu.",
                )

            # ------------------------------------------------------------------
            # Provider succeeded / reconciliation required
            # ------------------------------------------------------------------

            if operation.status in (
                StoredCardOperationStatus.PROVIDER_SUCCEEDED,
                StoredCardOperationStatus.RECONCILIATION_REQUIRED,
            ):
                return cls._reconcile_create_operation(
                    operation_id=operation.id,
                )

            # ------------------------------------------------------------------
            # Existing pending operation
            # ------------------------------------------------------------------

            if operation.status == StoredCardOperationStatus.PENDING:
                if cls._is_operation_stale(operation=operation):
                    cls._mark_reconciliation_required(
                        operation_id=operation.id,
                    )

                    raise CardStorageConsistencyError(
                        "Önceki kart kayıt işlemi uzun süredir "
                        "sonuçlanmadı. Kart durumu doğrulanmadan "
                        "yeni kayıt işlemi başlatılamaz."
                    )

                raise CardStorageOperationInProgressError(
                    "Bu kullanıcı için başka bir kart kayıt işlemi "
                    "halen devam ediyor."
                )

            raise CardStorageConsistencyError(
                "Geçersiz kart operation durumu."
            )

        # ------------------------------------------------------------------
        # Payment customer
        # ------------------------------------------------------------------

        payment_customer = cls._get_payment_customer(
            user=user,
        )

        conversation_id = uuid.uuid4().hex

        request_payload = cls._build_create_request(
            user=user,
            payment_customer=payment_customer,
            card=card,
            conversation_id=conversation_id,
            external_id=str(operation.id),
        )

        # ------------------------------------------------------------------
        # Provider
        # ------------------------------------------------------------------

        try:
            response = cls._create_remote(
                request_payload=request_payload,
            )

        except CardStorageGatewayError:
            cls._mark_reconciliation_required(
                operation_id=operation.id,
            )
            raise

        # ------------------------------------------------------------------
        # Provider failure
        # ------------------------------------------------------------------

        status = cls._clean(
            response.get("status"),
        ).lower()

        if status != "success":
            error_code = cls._clean(
                response.get("errorCode"),
            )

            error_message = cls._clean(
                response.get("errorMessage"),
            )

            cls._mark_operation_failed(
                operation_id=operation.id,
                error_code=error_code,
                error_message=error_message,
            )

            raise PaymentValidationError(
                error_message
                or "Kart kaydedilemedi.",
            )

        # ------------------------------------------------------------------
        # Normalize provider response
        # ------------------------------------------------------------------

        provider_card = cls._normalize_create_response(
            response=response,
            expected_conversation_id=conversation_id,
            expected_external_id=(
                str(operation.id)
                if payment_customer is None
                else None
            ),
            expected_card_user_key=(
                payment_customer.provider_customer_key
                if payment_customer
                else None
            ),
            expire_month=card.expire_month,
            expire_year=card.expire_year,
        )

        # ------------------------------------------------------------------
        # Provider success is persisted BEFORE local card persistence.
        # ------------------------------------------------------------------

        cls._mark_provider_succeeded(
            operation_id=operation.id,
            provider_card=provider_card,
        )

        # ------------------------------------------------------------------
        # Local persistence
        # ------------------------------------------------------------------

        try:
            return cls._persist_created_card(
                operation_id=operation.id,
                user=user,
                provider_card=provider_card,
            )

        except Exception as exc:
            logger.exception(
                "Stored card local persistence failed after "
                "iyzico success.",
            )

            raise CardStorageConsistencyError(
                "Kart iyzico tarafında oluşturuldu fakat "
                "yerel kayıt tamamlanamadı.",
            ) from exc

    # ==========================================================================
    # LIST
    # ==========================================================================

    @classmethod
    def list_cards(
        cls,
        *,
        user,
        active_only=True,
    ):
        cls._validate_user(user=user)

        queryset = (
            StoredCard.objects
            .filter(
                payment_customer__user=user,
                payment_customer__provider=cls.PROVIDER,
            )
            .select_related(
                "payment_customer",
            )
        )

        if active_only:
            queryset = queryset.filter(
                is_active=True,
            )

        return list(
            queryset.order_by(
                "-is_default",
                "-created_at",
                "-pk",
            )
        )

    # ==========================================================================
    # SYNC
    # ==========================================================================

    @classmethod
    def sync_cards(
        cls,
        *,
        user,
    ):
        cls._validate_user(user=user)

        payment_customer = cls._get_payment_customer(
            user=user,
        )

        if not payment_customer:
            return []

        provider_customer_key = cls._clean(
            payment_customer.provider_customer_key,
        )

        if not provider_customer_key:
            raise CardStorageConsistencyError(
                "PaymentCustomer içerisinde iyzico cardUserKey bulunmuyor."
            )

        response = cls._list_remote(
            card_user_key=provider_customer_key,
        )

        status = cls._clean(
            response.get("status"),
        ).lower()

        if status != "success":
            raise CardStorageGatewayError(
                "iyzico kayıtlı kart listesi alınamadı. "
                f"{cls._clean(response.get('errorMessage'))}"
            )

        if "cardDetails" not in response:
            raise CardStorageGatewayError(
                "iyzico kart listesi response'unda "
                "cardDetails alanı bulunmuyor."
            )

        raw_cards = response["cardDetails"]

        if not isinstance(raw_cards, list):
            raise CardStorageGatewayError(
                "iyzico cardDetails beklenmeyen formatta."
            )

        response_customer_key = cls._clean(
            response.get("cardUserKey"),
        )

        if (
            response_customer_key
            and response_customer_key != provider_customer_key
        ):
            raise CardStorageConsistencyError(
                "iyzico cardUserKey local kayıt ile eşleşmiyor."
            )

        provider_cards = [
            cls._normalize_list_card(
                card_data=item,
                card_user_key=provider_customer_key,
            )
            for item in raw_cards
        ]

        with transaction.atomic():
            locked_customer = (
                PaymentCustomer.objects
                .select_for_update()
                .get(
                    pk=payment_customer.pk,
                )
            )

            old_default_token = (
                StoredCard.objects
                .filter(
                    payment_customer=locked_customer,
                    is_active=True,
                    is_default=True,
                )
                .values_list(
                    "provider_card_token",
                    flat=True,
                )
                .first()
            )

            # Provider source of truth.
            StoredCard.objects.filter(
                payment_customer=locked_customer,
            ).update(
                is_active=False,
                is_default=False,
            )

            for provider_card in provider_cards:
                existing = (
                    StoredCard.objects
                    .filter(
                        payment_customer=locked_customer,
                        provider_card_token=(
                            provider_card.card_token
                        ),
                    )
                    .first()
                )

                expire_month = provider_card.expire_month
                expire_year = provider_card.expire_year

                if existing:
                    if not expire_month:
                        expire_month = existing.expire_month

                    if not expire_year:
                        expire_year = existing.expire_year

                is_expired = cls._is_expired(
                    expire_month=expire_month,
                    expire_year=expire_year,
                )

                StoredCard.objects.update_or_create(
                    payment_customer=locked_customer,
                    provider_card_token=(
                        provider_card.card_token
                    ),
                    defaults={
                        "card_alias": provider_card.card_alias,
                        "bin_number": provider_card.bin_number,
                        "last_four_digits": (
                            provider_card.last_four_digits
                        ),
                        "card_type": provider_card.card_type,
                        "card_association": (
                            provider_card.card_association
                        ),
                        "card_family": provider_card.card_family,
                        "card_bank_code": (
                            provider_card.card_bank_code
                        ),
                        "card_bank_name": (
                            provider_card.card_bank_name
                        ),
                        "expire_month": expire_month,
                        "expire_year": expire_year,
                        "is_active": not is_expired,
                        "is_default": False,
                    },
                )

            # ------------------------------------------------------------------
            # Preserve old default if it still exists.
            # ------------------------------------------------------------------

            default_card = None

            if old_default_token:
                default_card = (
                    StoredCard.objects
                    .filter(
                        payment_customer=locked_customer,
                        provider_card_token=old_default_token,
                        is_active=True,
                    )
                    .first()
                )

            # ------------------------------------------------------------------
            # Otherwise choose latest active card.
            # ------------------------------------------------------------------

            if default_card is None:
                default_card = (
                    StoredCard.objects
                    .filter(
                        payment_customer=locked_customer,
                        is_active=True,
                    )
                    .order_by(
                        "-created_at",
                        "-pk",
                    )
                    .first()
                )

            if default_card:
                StoredCard.objects.filter(
                    payment_customer=locked_customer,
                ).update(
                    is_default=False,
                )

                default_card.is_default = True

                default_card.save(
                    update_fields=[
                        "is_default",
                        "updated_at",
                    ],
                )

        return cls.list_cards(
            user=user,
        )

    # ==========================================================================
    # DELETE
    # ==========================================================================

    @classmethod
    def delete_card(
        cls,
        *,
        user,
        card_id,
        idempotency_key,
    ):
        cls._validate_user(user=user)

        card = cls._get_user_card(
            user=user,
            card_id=card_id,
        )

        # Desired state zaten sağlanmış.
        if not card.is_active:
            return card

        idempotency_key = cls._normalize_idempotency_key(
            idempotency_key,
        )

        request_fingerprint = cls._build_delete_fingerprint(
            card=card,
        )

        operation, created = cls._get_or_create_operation(
            user=user,
            operation_type=StoredCardOperationType.DELETE,
            idempotency_key=idempotency_key,
            request_fingerprint=request_fingerprint,
            stored_card=card,
        )

        if not created:
            # ------------------------------------------------------------------
            # Already completed
            # ------------------------------------------------------------------

            if operation.status == StoredCardOperationStatus.SUCCESS:
                return cls._get_operation_card(
                    operation=operation,
                )

            # ------------------------------------------------------------------
            # Previous failure
            # ------------------------------------------------------------------

            if operation.status == StoredCardOperationStatus.FAILED:
                raise CardStorageGatewayError(
                    operation.error_message
                    or "Kart silme işlemi başarısız oldu.",
                )

            # ------------------------------------------------------------------
            # Provider succeeded / reconciliation required
            # ------------------------------------------------------------------

            if operation.status in (
                StoredCardOperationStatus.PROVIDER_SUCCEEDED,
                StoredCardOperationStatus.RECONCILIATION_REQUIRED,
            ):
                return cls._reconcile_delete_operation(
                    operation_id=operation.id,
                )

            # ------------------------------------------------------------------
            # Existing pending operation
            # ------------------------------------------------------------------

            if operation.status == StoredCardOperationStatus.PENDING:
                if cls._is_operation_stale(operation=operation):
                    cls._mark_reconciliation_required(
                        operation_id=operation.id,
                    )

                    raise CardStorageConsistencyError(
                        "Kart silme işleminin sonucu kesin olarak "
                        "belirlenemedi."
                    )

                raise CardStorageOperationInProgressError(
                    "Bu kart için başka bir silme işlemi halen devam ediyor."
                )

            raise CardStorageConsistencyError(
                "Geçersiz kart silme operation durumu."
            )

        # ------------------------------------------------------------------
        # Provider request
        # ------------------------------------------------------------------

        payment_customer = card.payment_customer

        provider_customer_key = cls._clean(
            payment_customer.provider_customer_key,
        )

        if not provider_customer_key:
            raise CardStorageConsistencyError(
                "Kartın provider customer key bilgisi bulunmuyor."
            )

        conversation_id = uuid.uuid4().hex

        request_payload = {
            "locale": cls.LOCALE,
            "conversationId": conversation_id,
            "cardUserKey": provider_customer_key,
            "cardToken": card.provider_card_token,
        }

        try:
            response = cls._delete_remote(
                request_payload=request_payload,
            )

        except CardStorageGatewayError:
            cls._mark_reconciliation_required(
                operation_id=operation.id,
            )
            raise

        status = cls._clean(
            response.get("status"),
        ).lower()

        error_code = cls._clean(
            response.get("errorCode"),
        )

        # 3006 = cardToken bulunamadı.
        # DELETE açısından istenen state zaten sağlanmıştır.
        if (
            status != "success"
            and error_code != cls.CARD_NOT_FOUND_CODE
        ):
            cls._mark_operation_failed(
                operation_id=operation.id,
                error_code=error_code,
                error_message=cls._clean(
                    response.get("errorMessage"),
                ),
            )

            raise CardStorageGatewayError(
                cls._clean(
                    response.get("errorMessage"),
                )
                or "Kayıtlı kart silinemedi.",
            )

        # ------------------------------------------------------------------
        # Provider success persisted
        # ------------------------------------------------------------------

        cls._mark_provider_succeeded_for_delete(
            operation_id=operation.id,
            card=card,
        )

        try:
            return cls._persist_deleted_card(
                operation_id=operation.id,
            )

        except Exception as exc:
            logger.exception(
                "Stored card local delete persistence failed "
                "after iyzico success.",
            )

            raise CardStorageConsistencyError(
                "Kart iyzico tarafında silindi fakat "
                "yerel kayıt güncellenemedi.",
            ) from exc

    # ==========================================================================
    # DEFAULT
    # ==========================================================================

    @classmethod
    def set_default_card(
        cls,
        *,
        user,
        card_id,
    ):
        cls._validate_user(user=user)

        with transaction.atomic():
            card = (
                StoredCard.objects
                .select_for_update()
                .select_related(
                    "payment_customer",
                )
                .filter(
                    pk=card_id,
                    payment_customer__user=user,
                    payment_customer__provider=cls.PROVIDER,
                    is_active=True,
                )
                .first()
            )

            if not card:
                raise StoredCardNotFoundError(
                    "Aktif kayıtlı kart bulunamadı.",
                )

            payment_customer = (
                PaymentCustomer.objects
                .select_for_update()
                .get(
                    pk=card.payment_customer_id,
                )
            )

            StoredCard.objects.filter(
                payment_customer=payment_customer,
            ).update(
                is_default=False,
            )

            card.is_default = True

            card.save(
                update_fields=[
                    "is_default",
                    "updated_at",
                ],
            )

        return card

    # ==========================================================================
    # CREATE RECONCILIATION
    # ==========================================================================

    @classmethod
    def _reconcile_create_operation(
        cls,
        *,
        operation_id,
    ):
        operation = (
            StoredCardOperation.objects
            .select_related("user")
            .get(
                pk=operation_id,
            )
        )

        if operation.status == StoredCardOperationStatus.SUCCESS:
            return cls._get_operation_card(
                operation=operation,
            )

        if not operation.provider_card_token:
            try:
                cls.sync_cards(
                    user=operation.user,
                )
            except Exception:
                logger.exception(
                    "Stored card reconciliation sync failed.",
                )

            raise CardStorageConsistencyError(
                "Kart kayıt işleminin provider sonucu kesin olarak "
                "belirlenemedi. Kart durumu tekrar doğrulanmalıdır."
            )

        payment_customer = (
            cls._ensure_payment_customer_from_operation(
                operation=operation,
            )
        )

        cls.sync_cards(
            user=operation.user,
        )

        card = (
            StoredCard.objects
            .select_related("payment_customer")
            .filter(
                payment_customer=payment_customer,
                provider_card_token=(
                    operation.provider_card_token
                ),
            )
            .first()
        )

        if not card:
            raise CardStorageConsistencyError(
                "iyzico tarafından oluşturulan kart "
                "senkronizasyonda bulunamadı."
            )

        if (
            operation.provider_customer_key
            and card.payment_customer.provider_customer_key
            != operation.provider_customer_key
        ):
            raise CardStorageConsistencyError(
                "Kartın provider customer ilişkisi "
                "operation ile eşleşmiyor."
            )

        with transaction.atomic():
            locked_operation = (
                StoredCardOperation.objects
                .select_for_update()
                .get(
                    pk=operation.id,
                )
            )

            if (
                locked_operation.status
                == StoredCardOperationStatus.SUCCESS
            ):
                return cls._get_operation_card(
                    operation=locked_operation,
                )

            if locked_operation.make_default:
                StoredCard.objects.filter(
                    payment_customer=payment_customer,
                ).update(
                    is_default=False,
                )

                card.is_default = True

                card.save(
                    update_fields=[
                        "is_default",
                        "updated_at",
                    ],
                )

            locked_operation.stored_card = card
            locked_operation.status = (
                StoredCardOperationStatus.SUCCESS
            )
            locked_operation.completed_at = timezone.now()

            locked_operation.save(
                update_fields=[
                    "stored_card",
                    "status",
                    "completed_at",
                    "updated_at",
                ],
            )

        return card

    # ==========================================================================
    # DELETE RECONCILIATION
    # ==========================================================================

    @classmethod
    def _reconcile_delete_operation(
        cls,
        *,
        operation_id,
    ):
        operation = (
            StoredCardOperation.objects
            .select_related(
                "user",
                "stored_card",
            )
            .get(
                pk=operation_id,
            )
        )

        if operation.status == StoredCardOperationStatus.SUCCESS:
            return cls._get_operation_card(
                operation=operation,
            )

        if not operation.stored_card_id:
            raise CardStorageConsistencyError(
                "Delete operation bir karta bağlı değil.",
            )

        cls.sync_cards(
            user=operation.user,
        )

        card = (
            StoredCard.objects
            .select_related("payment_customer")
            .filter(
                pk=operation.stored_card_id,
            )
            .first()
        )

        if not card:
            with transaction.atomic():
                locked_operation = (
                    StoredCardOperation.objects
                    .select_for_update()
                    .get(
                        pk=operation.id,
                    )
                )

                locked_operation.status = (
                    StoredCardOperationStatus.SUCCESS
                )
                locked_operation.completed_at = timezone.now()

                locked_operation.save(
                    update_fields=[
                        "status",
                        "completed_at",
                        "updated_at",
                    ],
                )

            return None

        # Sync sonrası inactive ise provider'da kart yok.
        if not card.is_active:
            with transaction.atomic():
                locked_operation = (
                    StoredCardOperation.objects
                    .select_for_update()
                    .get(
                        pk=operation.id,
                    )
                )

                locked_operation.status = (
                    StoredCardOperationStatus.SUCCESS
                )
                locked_operation.completed_at = timezone.now()

                locked_operation.save(
                    update_fields=[
                        "status",
                        "completed_at",
                        "updated_at",
                    ],
                )

            return card

        # Provider'da kart hâlâ aktif.
        payment_customer = card.payment_customer

        request_payload = {
            "locale": cls.LOCALE,
            "conversationId": uuid.uuid4().hex,
            "cardUserKey": (
                payment_customer.provider_customer_key
            ),
            "cardToken": card.provider_card_token,
        }

        try:
            response = cls._delete_remote(
                request_payload=request_payload,
            )

        except CardStorageGatewayError:
            raise

        status = cls._clean(
            response.get("status"),
        ).lower()

        error_code = cls._clean(
            response.get("errorCode"),
        )

        if (
            status != "success"
            and error_code != cls.CARD_NOT_FOUND_CODE
        ):
            raise CardStorageGatewayError(
                cls._clean(
                    response.get("errorMessage"),
                )
                or "Kart silinemedi.",
            )

        cls._mark_provider_succeeded_for_delete(
            operation_id=operation.id,
            card=card,
        )

        return cls._persist_deleted_card(
            operation_id=operation.id,
        )

    # ==========================================================================
    # OPERATION CREATION
    # ==========================================================================

    @classmethod
    def _get_or_create_operation(
        cls,
        *,
        user,
        operation_type,
        idempotency_key,
        request_fingerprint,
        stored_card=None,
        make_default=False,
    ):
        """
        Exact idempotency operation varsa onu döndürür.

        Yeni operation oluşturulursa ``(operation, True)`` döner.
        Mevcut operation kullanılırsa ``(operation, False)`` döner.

        Active-operation unique constraint'i nedeniyle farklı bir
        idempotency key ile aynı anda ikinci bir CREATE/DELETE başlatılmaz.
        Stale active operation varsa yeni remote işlem açmak yerine
        reconciliation gerekir.
        """

        active_statuses = [
            StoredCardOperationStatus.PENDING,
            StoredCardOperationStatus.PROVIDER_SUCCEEDED,
            StoredCardOperationStatus.RECONCILIATION_REQUIRED,
        ]

        for _ in range(2):
            existing = (
                StoredCardOperation.objects
                .filter(
                    user=user,
                    provider=cls.PROVIDER,
                    operation_type=operation_type,
                    idempotency_key=idempotency_key,
                )
                .first()
            )

            if existing:
                cls._validate_operation_fingerprint(
                    operation=existing,
                    request_fingerprint=request_fingerprint,
                )

                return existing, False

            # Same logical operation already in progress with another
            # idempotency key. Check before INSERT for the normal path.
            if operation_type == StoredCardOperationType.CREATE:
                active_operation = (
                    StoredCardOperation.objects
                    .filter(
                        user=user,
                        provider=cls.PROVIDER,
                        operation_type=StoredCardOperationType.CREATE,
                        status__in=active_statuses,
                    )
                    .order_by("-created_at", "-pk")
                    .first()
                )
            else:
                active_operation = (
                    StoredCardOperation.objects
                    .filter(
                        stored_card=stored_card,
                        provider=cls.PROVIDER,
                        operation_type=StoredCardOperationType.DELETE,
                        status__in=active_statuses,
                    )
                    .order_by("-created_at", "-pk")
                    .first()
                )

            if active_operation:
                if cls._is_operation_stale(
                    operation=active_operation,
                ):
                    cls._mark_reconciliation_required(
                        operation_id=active_operation.id,
                    )

                    raise CardStorageConsistencyError(
                        "Önceki kart operation işleminin sonucu "
                        "kesin olarak belirlenemedi. Önce reconciliation "
                        "tamamlanmalıdır."
                    )

                raise CardStorageOperationInProgressError(
                    "Aynı kart operation işlemi halen devam ediyor."
                )

            try:
                with transaction.atomic():
                    operation = (
                        StoredCardOperation.objects.create(
                            user=user,
                            provider=cls.PROVIDER,
                            operation_type=operation_type,
                            idempotency_key=idempotency_key,
                            request_fingerprint=request_fingerprint,
                            stored_card=stored_card,
                            make_default=make_default,
                            status=StoredCardOperationStatus.PENDING,
                        )
                    )

                return operation, True

            except IntegrityError:
                # Which constraint failed is provider/DB specific; first
                # re-check the exact idempotency record, then the active
                # logical operation.
                existing = (
                    StoredCardOperation.objects
                    .filter(
                        user=user,
                        provider=cls.PROVIDER,
                        operation_type=operation_type,
                        idempotency_key=idempotency_key,
                    )
                    .first()
                )

                if existing:
                    cls._validate_operation_fingerprint(
                        operation=existing,
                        request_fingerprint=request_fingerprint,
                    )

                    return existing, False

                if operation_type == StoredCardOperationType.CREATE:
                    active_operation = (
                        StoredCardOperation.objects
                        .filter(
                            user=user,
                            provider=cls.PROVIDER,
                            operation_type=StoredCardOperationType.CREATE,
                            status__in=active_statuses,
                        )
                        .order_by("-created_at", "-pk")
                        .first()
                    )
                else:
                    active_operation = (
                        StoredCardOperation.objects
                        .filter(
                            stored_card=stored_card,
                            provider=cls.PROVIDER,
                            operation_type=StoredCardOperationType.DELETE,
                            status__in=active_statuses,
                        )
                        .order_by("-created_at", "-pk")
                        .first()
                    )

                if active_operation:
                    if cls._is_operation_stale(
                        operation=active_operation,
                    ):
                        cls._mark_reconciliation_required(
                            operation_id=active_operation.id,
                        )

                        raise CardStorageConsistencyError(
                            "Önceki kart operation işleminin sonucu "
                            "kesin olarak belirlenemedi. Önce reconciliation "
                            "tamamlanmalıdır."
                        )

                    raise CardStorageOperationInProgressError(
                        "Aynı kart operation işlemi halen devam ediyor.",
                    )

        raise CardStorageConsistencyError(
            "Kart operation kaydı oluşturulamadı.",
        )

    # ==========================================================================
    # PERSIST CREATE
    # ==========================================================================

    @classmethod
    def _persist_created_card(
        cls,
        *,
        operation_id,
        user,
        provider_card,
    ):
        with transaction.atomic():
            operation = (
                StoredCardOperation.objects
                .select_for_update()
                .get(
                    pk=operation_id,
                )
            )

            if (
                operation.provider_card_token
                and operation.provider_card_token
                != provider_card.card_token
            ):
                raise CardStorageConsistencyError(
                    "Operation provider card token ile response "
                    "card token eşleşmiyor."
                )

            if (
                operation.provider_customer_key
                and operation.provider_customer_key
                != provider_card.card_user_key
            ):
                raise CardStorageConsistencyError(
                    "Operation provider customer key ile response "
                    "card user key eşleşmiyor."
                )

            payment_customer = (
                PaymentCustomer.objects
                .select_for_update()
                .filter(
                    user=user,
                    provider=cls.PROVIDER,
                )
                .first()
            )

            if payment_customer:
                if (
                    payment_customer.provider_customer_key
                    != provider_card.card_user_key
                ):
                    raise CardStorageConsistencyError(
                        "iyzico cardUserKey local kayıt ile "
                        "eşleşmiyor."
                    )

            else:
                try:
                    payment_customer = (
                        PaymentCustomer.objects.create(
                            user=user,
                            provider=cls.PROVIDER,
                            provider_customer_key=(
                                provider_card.card_user_key
                            ),
                        )
                    )

                except IntegrityError:
                    payment_customer = (
                        PaymentCustomer.objects
                        .select_for_update()
                        .filter(
                            user=user,
                            provider=cls.PROVIDER,
                        )
                        .first()
                    )

                    if not payment_customer:
                        raise CardStorageConsistencyError(
                            "PaymentCustomer oluşturulamadı."
                        )

                    if (
                        payment_customer.provider_customer_key
                        != provider_card.card_user_key
                    ):
                        raise CardStorageConsistencyError(
                            "Provider customer key çakışması."
                        )

            card, _ = (
                StoredCard.objects
                .update_or_create(
                    payment_customer=payment_customer,
                    provider_card_token=(
                        provider_card.card_token
                    ),
                    defaults={
                        "card_alias": (
                            provider_card.card_alias
                        ),
                        "bin_number": (
                            provider_card.bin_number
                        ),
                        "last_four_digits": (
                            provider_card.last_four_digits
                        ),
                        "card_type": (
                            provider_card.card_type
                        ),
                        "card_association": (
                            provider_card.card_association
                        ),
                        "card_family": (
                            provider_card.card_family
                        ),
                        "card_bank_code": (
                            provider_card.card_bank_code
                        ),
                        "card_bank_name": (
                            provider_card.card_bank_name
                        ),
                        "expire_month": (
                            provider_card.expire_month
                        ),
                        "expire_year": (
                            provider_card.expire_year
                        ),
                        "is_active": True,
                    },
                )
            )

            if operation.make_default:
                StoredCard.objects.filter(
                    payment_customer=payment_customer,
                ).exclude(
                    pk=card.pk,
                ).update(
                    is_default=False,
                )

                card.is_default = True

                card.save(
                    update_fields=[
                        "is_default",
                        "updated_at",
                    ],
                )

            else:
                has_default = (
                    StoredCard.objects
                    .filter(
                        payment_customer=payment_customer,
                        is_active=True,
                        is_default=True,
                    )
                    .exclude(
                        pk=card.pk,
                    )
                    .exists()
                )

                if not has_default:
                    StoredCard.objects.filter(
                        payment_customer=payment_customer,
                    ).exclude(
                        pk=card.pk,
                    ).update(
                        is_default=False,
                    )

                    card.is_default = True

                    card.save(
                        update_fields=[
                            "is_default",
                            "updated_at",
                        ],
                    )

            operation.stored_card = card
            operation.status = (
                StoredCardOperationStatus.SUCCESS
            )
            operation.completed_at = timezone.now()

            operation.save(
                update_fields=[
                    "stored_card",
                    "status",
                    "completed_at",
                    "updated_at",
                ],
            )

        return card

    # ==========================================================================
    # PERSIST DELETE
    # ==========================================================================

    @classmethod
    def _persist_deleted_card(
        cls,
        *,
        operation_id,
    ):
        with transaction.atomic():
            operation = (
                StoredCardOperation.objects
                .select_for_update()
                .select_related(
                    "stored_card",
                )
                .get(
                    pk=operation_id,
                )
            )

            card = operation.stored_card

            if card:
                card = (
                    StoredCard.objects
                    .select_for_update()
                    .select_related(
                        "payment_customer",
                    )
                    .get(
                        pk=card.pk,
                    )
                )

                was_default = card.is_default

                card.is_active = False
                card.is_default = False

                card.save(
                    update_fields=[
                        "is_active",
                        "is_default",
                        "updated_at",
                    ],
                )

                if was_default:
                    replacement = (
                        StoredCard.objects
                        .filter(
                            payment_customer=(
                                card.payment_customer
                            ),
                            is_active=True,
                        )
                        .order_by(
                            "-created_at",
                            "-pk",
                        )
                        .first()
                    )

                    if replacement:
                        StoredCard.objects.filter(
                            payment_customer=(
                                card.payment_customer
                            ),
                        ).update(
                            is_default=False,
                        )

                        replacement.is_default = True

                        replacement.save(
                            update_fields=[
                                "is_default",
                                "updated_at",
                            ],
                        )

            operation.status = (
                StoredCardOperationStatus.SUCCESS
            )
            operation.completed_at = timezone.now()

            operation.save(
                update_fields=[
                    "status",
                    "completed_at",
                    "updated_at",
                ],
            )

        return card

    # ==========================================================================
    # PROVIDER SUCCESS STATE
    # ==========================================================================

    @classmethod
    def _mark_provider_succeeded(
        cls,
        *,
        operation_id,
        provider_card,
    ):
        with transaction.atomic():
            operation = (
                StoredCardOperation.objects
                .select_for_update()
                .get(
                    pk=operation_id,
                )
            )

            if (
                operation.provider_card_token
                and operation.provider_card_token
                != provider_card.card_token
            ):
                raise CardStorageConsistencyError(
                    "Operation daha önce farklı bir provider card "
                    "token ile işaretlenmiş."
                )

            if (
                operation.provider_customer_key
                and operation.provider_customer_key
                != provider_card.card_user_key
            ):
                raise CardStorageConsistencyError(
                    "Operation daha önce farklı bir provider customer "
                    "key ile işaretlenmiş."
                )

            operation.provider_card_token = (
                provider_card.card_token
            )

            operation.provider_customer_key = (
                provider_card.card_user_key
            )

            operation.provider_external_id = (
                provider_card.provider_external_id
            )

            operation.status = (
                StoredCardOperationStatus.PROVIDER_SUCCEEDED
            )

            operation.save(
                update_fields=[
                    "provider_card_token",
                    "provider_customer_key",
                    "provider_external_id",
                    "status",
                    "updated_at",
                ],
            )

    @classmethod
    def _mark_provider_succeeded_for_delete(
        cls,
        *,
        operation_id,
        card,
    ):
        with transaction.atomic():
            operation = (
                StoredCardOperation.objects
                .select_for_update()
                .get(
                    pk=operation_id,
                )
            )

            operation.provider_card_token = (
                card.provider_card_token
            )

            operation.provider_customer_key = (
                card.payment_customer.provider_customer_key
            )

            operation.status = (
                StoredCardOperationStatus.PROVIDER_SUCCEEDED
            )

            operation.save(
                update_fields=[
                    "provider_card_token",
                    "provider_customer_key",
                    "status",
                    "updated_at",
                ],
            )

    # ==========================================================================
    # OPERATION STATE
    # ==========================================================================

    @classmethod
    def _mark_operation_failed(
        cls,
        *,
        operation_id,
        error_code,
        error_message,
    ):
        error_code = cls._clean(error_code)
        error_message = cls._clean(error_message)

        with transaction.atomic():
            operation = (
                StoredCardOperation.objects
                .select_for_update()
                .get(
                    pk=operation_id,
                )
            )

            operation.status = (
                StoredCardOperationStatus.FAILED
            )

            operation.error_code = error_code[:100]
            operation.error_message = error_message[:500]
            operation.completed_at = timezone.now()

            operation.save(
                update_fields=[
                    "status",
                    "error_code",
                    "error_message",
                    "completed_at",
                    "updated_at",
                ],
            )

    @classmethod
    def _mark_reconciliation_required(
        cls,
        *,
        operation_id,
    ):
        with transaction.atomic():
            operation = (
                StoredCardOperation.objects
                .select_for_update()
                .get(
                    pk=operation_id,
                )
            )

            if operation.status == StoredCardOperationStatus.SUCCESS:
                return

            operation.status = (
                StoredCardOperationStatus.RECONCILIATION_REQUIRED
            )

            operation.save(
                update_fields=[
                    "status",
                    "updated_at",
                ],
            )

    @staticmethod
    def _is_operation_stale(
        *,
        operation,
    ):
        return (
            timezone.now()
            - operation.updated_at
            >= CardStorageService.STALE_OPERATION_AFTER
        )

    # ==========================================================================
    # OPERATION VALIDATION
    # ==========================================================================

    @staticmethod
    def _validate_operation_fingerprint(
        *,
        operation,
        request_fingerprint,
    ):
        stored_fingerprint = (
            operation.request_fingerprint or ""
        )

        if not hmac.compare_digest(
            stored_fingerprint,
            request_fingerprint,
        ):
            raise PaymentValidationError(
                "Aynı idempotency key farklı bir işlem "
                "için kullanılamaz.",
            )

    @classmethod
    def _get_operation_card(
        cls,
        *,
        operation,
    ):
        if not operation.stored_card_id:
            raise CardStorageConsistencyError(
                "Operation kayıtlı kart ile eşleştirilemedi.",
            )

        card = (
            StoredCard.objects
            .select_related(
                "payment_customer",
            )
            .filter(
                pk=operation.stored_card_id,
                payment_customer__user=operation.user,
                payment_customer__provider=cls.PROVIDER,
            )
            .first()
        )

        if not card:
            raise CardStorageConsistencyError(
                "Operation'a bağlı StoredCard bulunamadı.",
            )

        return card

    # ==========================================================================
    # PROVIDER REQUESTS
    # ==========================================================================

    @classmethod
    def _build_create_request(
        cls,
        *,
        user,
        payment_customer,
        card,
        conversation_id,
        external_id,
    ):
        card_payload = {
            "cardAlias": (
                card.card_alias
                or card.card_holder_name
            ),
            "cardHolderName": card.card_holder_name,
            "cardNumber": card.card_number,
            "expireMonth": card.expire_month,
            "expireYear": card.expire_year,
        }

        payload = {
            "locale": cls.LOCALE,
            "conversationId": conversation_id,
            "card": card_payload,
        }

        if payment_customer:
            payload["cardUserKey"] = (
                payment_customer.provider_customer_key
            )
            return payload

        email = cls._clean(
            getattr(
                user,
                "email",
                "",
            )
        )

        if not email:
            raise PaymentValidationError(
                "Kayıtlı kart eklemek için e-posta adresi gereklidir.",
            )

        try:
            validate_email(email)
        except DjangoValidationError as exc:
            raise PaymentValidationError(
                "Kullanıcı e-posta adresi geçerli değil.",
            ) from exc

        payload["externalId"] = external_id
        payload["email"] = email

        return payload

    @classmethod
    def _create_remote(
        cls,
        *,
        request_payload,
    ):
        try:
            resource = iyzipay.Card().create(
                request_payload,
                cls._options(),
            )

        except Exception as exc:
            logger.exception(
                "iyzico Card Storage create transport error.",
            )

            raise CardStorageGatewayError(
                "iyzico kart kayıt servisine bağlanılamadı.",
            ) from exc

        return cls._read_json(resource)

    @classmethod
    def _list_remote(
        cls,
        *,
        card_user_key,
    ):
        request_payload = {
            "locale": cls.LOCALE,
            "conversationId": uuid.uuid4().hex,
            "cardUserKey": card_user_key,
        }

        try:
            resource = iyzipay.CardList().retrieve(
                request_payload,
                cls._options(),
            )

        except Exception as exc:
            logger.exception(
                "iyzico Card Storage list transport error.",
            )

            raise CardStorageGatewayError(
                "iyzico kayıtlı kartlar servisine bağlanılamadı.",
            ) from exc

        return cls._read_json(resource)

    @classmethod
    def _delete_remote(
        cls,
        *,
        request_payload,
    ):
        try:
            resource = iyzipay.Card().delete(
                request_payload,
                cls._options(),
            )

        except Exception as exc:
            logger.exception(
                "iyzico Card Storage delete transport error.",
            )

            raise CardStorageGatewayError(
                "iyzico kayıtlı kart silme servisine bağlanılamadı.",
            ) from exc

        return cls._read_json(resource)

    # ==========================================================================
    # PROVIDER RESPONSE
    # ==========================================================================

    @classmethod
    def _normalize_create_response(
        cls,
        *,
        response,
        expected_conversation_id,
        expected_external_id,
        expected_card_user_key,
        expire_month,
        expire_year,
    ):
        response_conversation_id = cls._clean(
            response.get("conversationId"),
        )

        if (
            response_conversation_id
            and response_conversation_id
            != expected_conversation_id
        ):
            raise CardStorageConsistencyError(
                "iyzico conversationId doğrulaması başarısız.",
            )

        response_external_id = cls._clean(
            response.get("externalId"),
        )

        if (
            response_external_id
            and response_external_id != expected_external_id
        ):
            raise CardStorageConsistencyError(
                "iyzico externalId doğrulaması başarısız.",
            )

        card_token = cls._clean(
            response.get("cardToken"),
        )

        card_user_key = cls._clean(
            response.get("cardUserKey"),
        )

        last_four_digits = cls._clean(
            response.get("lastFourDigits"),
        )

        if not card_token:
            raise CardStorageConsistencyError(
                "iyzico response içerisinde cardToken bulunmuyor.",
            )

        if not card_user_key:
            raise CardStorageConsistencyError(
                "iyzico response içerisinde cardUserKey bulunmuyor.",
            )

        if (
            expected_card_user_key
            and card_user_key != expected_card_user_key
        ):
            raise CardStorageConsistencyError(
                "iyzico response cardUserKey mevcut "
                "PaymentCustomer ile eşleşmiyor.",
            )

        if not last_four_digits:
            raise CardStorageConsistencyError(
                "iyzico response içerisinde lastFourDigits bulunmuyor.",
            )

        return ProviderStoredCardData(
            card_token=card_token,
            card_user_key=card_user_key,
            card_alias=cls._clean(
                response.get("cardAlias"),
            ),
            bin_number=cls._clean(
                response.get("binNumber"),
            ),
            last_four_digits=last_four_digits,
            card_type=cls._clean(
                response.get("cardType"),
            ),
            card_association=cls._clean(
                response.get("cardAssociation"),
            ),
            card_family=cls._clean(
                response.get("cardFamily"),
            ),
            card_bank_code=cls._normalize_optional_int(
                response.get("cardBankCode"),
            ),
            card_bank_name=cls._clean(
                response.get("cardBankName"),
            ),
            expire_month=expire_month,
            expire_year=expire_year,
            provider_external_id=cls._clean(
                response.get("externalId"),
            ),
        )

    @classmethod
    def _normalize_list_card(
        cls,
        *,
        card_data,
        card_user_key,
    ):
        if not isinstance(card_data, dict):
            raise CardStorageConsistencyError(
                "iyzico kart listesinde geçersiz kart objesi.",
            )

        card_token = cls._clean(
            card_data.get("cardToken"),
        )

        last_four_digits = cls._clean(
            card_data.get("lastFourDigits"),
        )

        if not card_token:
            raise CardStorageConsistencyError(
                "iyzico kart listesinde cardToken eksik.",
            )

        if not last_four_digits:
            raise CardStorageConsistencyError(
                "iyzico kart listesinde lastFourDigits eksik.",
            )

        return ProviderStoredCardData(
            card_token=card_token,
            card_user_key=card_user_key,
            card_alias=cls._clean(
                card_data.get("cardAlias"),
            ),
            bin_number=cls._clean(
                card_data.get("binNumber"),
            ),
            last_four_digits=last_four_digits,
            card_type=cls._clean(
                card_data.get("cardType"),
            ),
            card_association=cls._clean(
                card_data.get("cardAssociation"),
            ),
            card_family=cls._clean(
                card_data.get("cardFamily"),
            ),
            card_bank_code=cls._normalize_optional_int(
                card_data.get("cardBankCode"),
            ),
            card_bank_name=cls._clean(
                card_data.get("cardBankName"),
            ),
            expire_month=cls._clean(
                card_data.get("expireMonth"),
            ),
            expire_year=cls._clean(
                card_data.get("expireYear"),
            ),
        )

    # ==========================================================================
    # VALIDATION
    # ==========================================================================

    @classmethod
    def _normalize_card_data(
        cls,
        *,
        card,
    ):
        card_holder_name = cls._clean(
            card.card_holder_name,
        )

        if not card_holder_name:
            raise PaymentValidationError(
                "Kart üzerindeki isim gereklidir.",
            )

        if len(card_holder_name) > 255:
            raise PaymentValidationError(
                "Kart üzerindeki isim çok uzun.",
            )

        card_number = cls._normalize_card_number(
            card.card_number,
        )

        expire_month = cls._normalize_expire_month(
            card.expire_month,
        )

        expire_year = cls._normalize_expire_year(
            card.expire_year,
        )

        cls._validate_card_expiration(
            expire_month=expire_month,
            expire_year=expire_year,
        )

        card_alias = cls._clean(
            card.card_alias,
        )

        if len(card_alias) > 293:
            raise PaymentValidationError(
                "Kart takma adı çok uzun.",
            )

        return StoredCardCreateData(
            card_holder_name=card_holder_name,
            card_number=card_number,
            expire_month=expire_month,
            expire_year=expire_year,
            card_alias=card_alias,
        )

    @staticmethod
    def _normalize_card_number(
        value,
    ):
        digits = "".join(
            char
            for char in str(value or "")
            if char.isdigit()
        )

        if not 12 <= len(digits) <= 19:
            raise PaymentValidationError(
                "Kart numarası geçerli değil.",
            )

        if not CardStorageService._passes_luhn(
            digits,
        ):
            raise PaymentValidationError(
                "Kart numarası geçerli değil.",
            )

        return digits

    @staticmethod
    def _passes_luhn(
        card_number,
    ):
        total = 0
        parity = len(card_number) % 2

        for index, char in enumerate(card_number):
            digit = int(char)

            if index % 2 == parity:
                digit *= 2

                if digit > 9:
                    digit -= 9

            total += digit

        return total % 10 == 0

    @staticmethod
    def _normalize_expire_month(
        value,
    ):
        try:
            month = int(
                str(value).strip(),
            )
        except (
            TypeError,
            ValueError,
        ) as exc:
            raise PaymentValidationError(
                "Kart son kullanma ayı geçerli değil.",
            ) from exc

        if not 1 <= month <= 12:
            raise PaymentValidationError(
                "Kart son kullanma ayı geçerli değil.",
            )

        return f"{month:02d}"

    @staticmethod
    def _normalize_expire_year(
        value,
    ):
        try:
            year = int(
                str(value).strip(),
            )
        except (
            TypeError,
            ValueError,
        ) as exc:
            raise PaymentValidationError(
                "Kart son kullanma yılı geçerli değil.",
            ) from exc

        if 0 <= year < 100:
            year += 2000

        if not 2000 <= year <= 9999:
            raise PaymentValidationError(
                "Kart son kullanma yılı geçerli değil.",
            )

        return str(year)

    @staticmethod
    def _validate_card_expiration(
        *,
        expire_month,
        expire_year,
    ):
        now = timezone.now()

        current_period = (
            now.year,
            now.month,
        )

        card_period = (
            int(expire_year),
            int(expire_month),
        )

        if card_period < current_period:
            raise PaymentValidationError(
                "Kartın son kullanma tarihi geçmiş.",
            )

    @staticmethod
    def _validate_user(
        *,
        user,
    ):
        if not getattr(
            user,
            "is_authenticated",
            False,
        ):
            raise PaymentValidationError(
                "Kayıtlı kart özelliği için giriş yapmalısınız.",
            )

    @staticmethod
    def _normalize_idempotency_key(
        value,
    ):
        value = str(
            value or "",
        ).strip()

        if not value:
            raise PaymentValidationError(
                "Idempotency-Key gereklidir.",
            )

        if len(value) > 100:
            raise PaymentValidationError(
                "Idempotency-Key çok uzun.",
            )

        return value

    # ==========================================================================
    # FINGERPRINT
    # ==========================================================================

    @classmethod
    def _build_create_fingerprint(
        cls,
        *,
        card,
        make_default,
    ):
        payload = {
            "operation": "create",
            "card_holder_name": card.card_holder_name,
            "card_number": card.card_number,
            "expire_month": card.expire_month,
            "expire_year": card.expire_year,
            "card_alias": card.card_alias,
            "make_default": bool(make_default),
        }

        return cls._fingerprint(payload)

    @classmethod
    def _build_delete_fingerprint(
        cls,
        *,
        card,
    ):
        payload = {
            "operation": "delete",
            "card_id": card.pk,
            "provider_card_token": card.provider_card_token,
        }

        return cls._fingerprint(payload)

    @staticmethod
    def _fingerprint(
        payload,
    ):
        raw = json.dumps(
            payload,
            sort_keys=True,
            separators=(
                ",",
                ":",
            ),
            ensure_ascii=False,
        ).encode("utf-8")

        secret = str(
            settings.SECRET_KEY,
        ).encode("utf-8")

        return hmac.new(
            secret,
            raw,
            hashlib.sha256,
        ).hexdigest()

    # ==========================================================================
    # PAYMENT CUSTOMER
    # ==========================================================================

    @classmethod
    def _get_payment_customer(
        cls,
        *,
        user,
    ):
        return (
            PaymentCustomer.objects
            .filter(
                user=user,
                provider=cls.PROVIDER,
            )
            .first()
        )

    @classmethod
    def _ensure_payment_customer_from_operation(
        cls,
        *,
        operation,
    ):
        provider_customer_key = cls._clean(
            operation.provider_customer_key,
        )

        if not provider_customer_key:
            raise CardStorageConsistencyError(
                "Operation içerisinde provider customer key bulunmuyor.",
            )

        with transaction.atomic():
            payment_customer = (
                PaymentCustomer.objects
                .select_for_update()
                .filter(
                    user=operation.user,
                    provider=cls.PROVIDER,
                )
                .first()
            )

            if payment_customer:
                if (
                    payment_customer.provider_customer_key
                    != provider_customer_key
                ):
                    raise CardStorageConsistencyError(
                        "Provider customer key uyuşmazlığı.",
                    )

                return payment_customer

            try:
                return (
                    PaymentCustomer.objects.create(
                        user=operation.user,
                        provider=cls.PROVIDER,
                        provider_customer_key=(
                            provider_customer_key
                        ),
                    )
                )

            except IntegrityError:
                payment_customer = (
                    PaymentCustomer.objects
                    .select_for_update()
                    .filter(
                        user=operation.user,
                        provider=cls.PROVIDER,
                    )
                    .first()
                )

                if not payment_customer:
                    raise CardStorageConsistencyError(
                        "PaymentCustomer recovery başarısız.",
                    )

                if (
                    payment_customer.provider_customer_key
                    != provider_customer_key
                ):
                    raise CardStorageConsistencyError(
                        "Provider customer key uyuşmazlığı.",
                    )

                return payment_customer

    # ==========================================================================
    # USER CARD
    # ==========================================================================

    @classmethod
    def _get_user_card(
        cls,
        *,
        user,
        card_id,
    ):
        card = (
            StoredCard.objects
            .select_related(
                "payment_customer",
            )
            .filter(
                pk=card_id,
                payment_customer__user=user,
                payment_customer__provider=cls.PROVIDER,
            )
            .first()
        )

        if not card:
            raise StoredCardNotFoundError(
                "Kayıtlı kart bulunamadı.",
            )

        return card

    # ==========================================================================
    # UTILS
    # ==========================================================================

    @staticmethod
    def _options():
        return {
            "api_key": settings.IYZICO_API_KEY,
            "secret_key": settings.IYZICO_SECRET_KEY,
            "base_url": settings.IYZICO_BASE_URL,
        }

    @staticmethod
    def _read_json(
        resource,
    ):
        try:
            raw = resource.read()

            if isinstance(raw, bytes):
                raw = raw.decode("utf-8")

            if not raw:
                raise CardStorageGatewayError(
                    "iyzico boş response döndürdü.",
                )

            parsed = json.loads(raw)

            if not isinstance(parsed, dict):
                raise CardStorageGatewayError(
                    "iyzico response beklenen JSON object formatında değil.",
                )

            return parsed

        except CardStorageGatewayError:
            raise

        except Exception as exc:
            raise CardStorageGatewayError(
                "iyzico response JSON formatında değil.",
            ) from exc

    @staticmethod
    def _clean(
        value,
    ):
        if value is None:
            return ""

        return str(value).strip()

    @staticmethod
    def _normalize_optional_int(
        value,
    ):
        if value in (
            None,
            "",
        ):
            return None

        try:
            return int(value)

        except (
            TypeError,
            ValueError,
        ):
            return None

    @staticmethod
    def _is_expired(
        *,
        expire_month,
        expire_year,
    ):
        if not expire_month or not expire_year:
            return False

        try:
            card_period = (
                int(expire_year),
                int(expire_month),
            )
        except (
            TypeError,
            ValueError,
        ):
            return False

        now = timezone.now()

        return card_period < (
            now.year,
            now.month,
        )