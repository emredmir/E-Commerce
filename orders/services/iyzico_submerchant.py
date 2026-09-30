import json
import logging
import re
import uuid
from datetime import timedelta

import iyzipay

from django.conf import settings
from django.core.exceptions import ObjectDoesNotExist, ValidationError
from django.core.validators import validate_email
from django.db import transaction
from django.utils import timezone

from accounts.models import (
    IyzicoOnboardingStatus,
    SellerProfile,
    SellerType,
)
from orders.exceptions import OrderDomainError


logger = logging.getLogger(__name__)


class SubmerchantError(OrderDomainError):
    """Base exception for iyzico submerchant operations."""


class SubmerchantValidationError(SubmerchantError):
    """Local seller data is invalid for iyzico onboarding."""


class SubmerchantAlreadyOnboardedError(SubmerchantError):
    """Seller already has an active iyzico submerchant."""


class SubmerchantOnboardingInProgressError(SubmerchantError):
    """Seller onboarding is currently in progress."""


class SubmerchantReconciliationRequiredError(SubmerchantError):
    """
    Local/remote iyzico state is ambiguous and must be reconciled.
    """


class IyzicoGatewayError(SubmerchantError):
    """Known iyzico remote/API/business failure."""


class IyzicoTransportError(IyzicoGatewayError):
    """Network/transport failure with ambiguous remote state."""


class IyzicoResponseError(IyzicoGatewayError):
    """Malformed or unexpected iyzico response."""


class IyzicoSubmerchantService:
    """
    Handles iyzico Marketplace submerchant onboarding.

    Important architecture rules:

    1. Local DB transactions are kept short.
    2. iyzico HTTP calls are never performed inside transaction.atomic().
    3. subMerchantExternalId is stable and is the reconciliation key.
    4. Ambiguous CREATE requests are never blindly retried.
    5. PENDING states are protected against becoming permanent.
    """

    LOCALE = "tr"
    CURRENCY = "TRY"

    # iyzico Marketplace error kodları.
    NOT_FOUND_ERROR_CODE = "2001"
    DUPLICATE_EXTERNAL_ID_ERROR_CODE = "2002"

    PENDING_TIMEOUT = timedelta(minutes=10) # 10 dakikayı daha sonra ihtiyacına göre değiştirebilirsin.

    ADDRESS_MIN_LENGTH = 5
    ADDRESS_MAX_LENGTH = 255

    CONTACT_NAME_MIN_LENGTH = 2
    CONTACT_NAME_MAX_LENGTH = 100

    CONTACT_SURNAME_MIN_LENGTH = 2
    CONTACT_SURNAME_MAX_LENGTH = 100

    TAX_OFFICE_MIN_LENGTH = 3
    TAX_OFFICE_MAX_LENGTH = 255

    LEGAL_COMPANY_TITLE_MIN_LENGTH = 3
    LEGAL_COMPANY_TITLE_MAX_LENGTH = 255

    TAX_NUMBER_MIN_LENGTH = 2
    TAX_NUMBER_MAX_LENGTH = 100

    EXTERNAL_ID_MIN_LENGTH = 1
    EXTERNAL_ID_MAX_LENGTH = 100

    NAME_MIN_LENGTH = 1
    NAME_MAX_LENGTH = 255

    PHONE_MAX_LENGTH = 25

    # ------------------------------------------------------------------
    # PUBLIC API
    # ------------------------------------------------------------------

    @classmethod
    def create(cls, *, seller_profile_id):
        """
        Start iyzico submerchant onboarding.

        Normal flow:

            local validation
                ↓
            PENDING
                ↓
            CREATE
                ↓
            success → ACTIVE

        Ambiguous flow:

            CREATE timeout / malformed response
                ↓
            RETRIEVE
                ├── found    → ACTIVE
                ├── 2001     → one CREATE retry
                └── unknown  → RECONCILIATION_REQUIRED

        Stale PENDING:

            PENDING > PENDING_TIMEOUT
                ↓
            RECONCILIATION
                ↓
            never blindly CREATE
        """

        cls._validate_settings()

        payload = cls._prepare_create(
            seller_profile_id=seller_profile_id,
        )

        # Eski bir PENDING, _prepare_create() içinde RECONCILIATION_REQUIRED durumuna dönüştürülür.
        # Bu durumda, başka bir CREATE işlemine izin vermeden önce reconciliation yapılmalıdır.
        if payload is None:
            return cls.reconcile(
                seller_profile_id=seller_profile_id,
            )

        try:
            response = cls._create_remote(
                payload=payload,
            )

        except (
            IyzicoTransportError,
            IyzicoResponseError,
        ) as exc:
            logger.warning(
                "Ambiguous iyzico submerchant CREATE result. "
                "seller_profile_id=%s external_id=%s error=%s",
                seller_profile_id,
                payload["subMerchantExternalId"],
                exc,
            )

            return cls._resolve_ambiguous_create(
                seller_profile_id=seller_profile_id,
                payload=payload,
                original_exception=exc,
            )

        return cls._handle_create_response(
            seller_profile_id=seller_profile_id,
            payload=payload,
            response=response,
        )

    @classmethod
    def reconcile(cls, *, seller_profile_id):
        """
        Synchronize local SellerProfile with iyzico.

        This method NEVER creates a submerchant.

        found:
            ACTIVE

        not_found:
            RECONCILIATION_REQUIRED

        unknown:
            RECONCILIATION_REQUIRED
        """

        cls._validate_settings()

        seller = cls._get_seller_for_reconciliation(
            seller_profile_id=seller_profile_id,
        )

        external_id = cls._clean(
            seller.iyzico_submerchant_external_id
        )

        cls._validate_external_id(
            external_id=external_id,
        )

        lookup = cls._retrieve_remote(
            external_id=external_id,
        )

        if lookup["state"] == "found":
            return cls._mark_active_from_remote(
                seller_profile_id=seller_profile_id,
                remote=lookup["response"],
                require_external_id=True,
            )

        if lookup["state"] == "not_found":
            cls._mark_reconciliation_required(
                seller_profile_id=seller_profile_id,
                error_code=lookup.get("error_code"),
                error_message=(
                    lookup.get("error_message")
                    or (
                        "iyzico tarafında subMerchantExternalId "
                        "ile eşleşen kayıt bulunamadı. "
                        "Tekrar doğrulama veya operasyonel "
                        "inceleme gerekiyor."
                    )
                ),
            )

            raise SubmerchantReconciliationRequiredError(
                "iyzico submerchant bulunamadı. "
                "Mutabakat gerekiyor."
            )

        cls._mark_reconciliation_required(
            seller_profile_id=seller_profile_id,
            error_code=lookup.get("error_code"),
            error_message=(
                lookup.get("error_message")
                or "iyzico submerchant durumu doğrulanamadı."
            ),
        )

        raise SubmerchantReconciliationRequiredError(
            "iyzico submerchant durumu doğrulanamadı. "
            "Mutabakat gerekiyor."
        )

    @classmethod
    def reset_reconciliation_for_retry(
        cls,
        *,
        seller_profile_id,
    ):
        """
        Explicitly move a seller from
        RECONCILIATION_REQUIRED → NOT_STARTED.

        This is intentionally NOT automatic.

        Before calling this method, the operator/admin should have
        confirmed that the remote submerchant does not exist or
        that another CREATE attempt is safe.
        """

        with transaction.atomic():
            seller = cls._get_seller_locked(
                seller_profile_id=seller_profile_id,
            )

            if (
                seller.iyzico_onboarding_status
                != IyzicoOnboardingStatus.RECONCILIATION_REQUIRED
            ):
                raise SubmerchantError(
                    "Seller reconciliation_required "
                    "durumunda değil."
                )

            if seller.iyzico_submerchant_key:
                raise SubmerchantAlreadyOnboardedError(
                    "Seller'ın subMerchantKey bilgisi mevcut. "
                    "Yeni submerchant oluşturulamaz."
                )

            now = timezone.now()

            seller.iyzico_onboarding_status = (
                IyzicoOnboardingStatus.NOT_STARTED
            )
            seller.iyzico_onboarding_started_at = None
            seller.iyzico_last_sync_at = now
            seller.iyzico_last_error_code = ""
            seller.iyzico_last_error_message = ""

            seller.save(
                update_fields=[
                    "iyzico_onboarding_status",
                    "iyzico_onboarding_started_at",
                    "iyzico_last_sync_at",
                    "iyzico_last_error_code",
                    "iyzico_last_error_message",
                ]
            )

        return seller

    # ------------------------------------------------------------------
    # CREATE PREPARATION
    # ------------------------------------------------------------------

    @classmethod
    def _prepare_create(cls, *, seller_profile_id):
        """
        Validate local data before persisting PENDING.

        Returns:
            payload dict  -> normal CREATE flow
            None          -> stale PENDING; reconcile first
        """

        conversation_id = uuid.uuid4().hex

        with transaction.atomic():
            seller = cls._get_seller_locked(
                seller_profile_id=seller_profile_id,
            )

            # ----------------------------------------------------------
            # PENDING
            # ----------------------------------------------------------

            if (
                seller.iyzico_onboarding_status
                == IyzicoOnboardingStatus.PENDING
            ):
                if cls._is_pending_stale(seller):
                    seller.iyzico_onboarding_status = (
                        IyzicoOnboardingStatus.RECONCILIATION_REQUIRED
                    )
                    seller.iyzico_last_sync_at = timezone.now()
                    seller.iyzico_last_error_code = ""
                    seller.iyzico_last_error_message = (
                        "Onboarding PENDING durumu "
                        "beklenen süreden uzun sürdü. "
                        "Remote state doğrulanmalıdır."
                    )

                    seller.save(
                        update_fields=[
                            "iyzico_onboarding_status",
                            "iyzico_last_sync_at",
                            "iyzico_last_error_code",
                            "iyzico_last_error_message",
                        ]
                    )

                    return None

                raise SubmerchantOnboardingInProgressError(
                    "Seller için iyzico onboarding işlemi "
                    "halen devam ediyor."
                )

            # ----------------------------------------------------------
            # Other local state validation
            # ----------------------------------------------------------

            cls._validate_local_state(
                seller=seller,
            )

            # ----------------------------------------------------------
            # Tüm doğrulamalar, PENDING durumu kalıcı olarak kaydedilmeden önce gerçekleştirilir.
            # ----------------------------------------------------------

            payload = cls._build_create_payload(
                seller=seller,
                conversation_id=conversation_id,
            )

            now = timezone.now()

            seller.iyzico_onboarding_status = (
                IyzicoOnboardingStatus.PENDING
            )
            seller.iyzico_onboarding_started_at = now
            seller.iyzico_last_sync_at = now
            seller.iyzico_last_error_code = ""
            seller.iyzico_last_error_message = ""

            seller.save(
                update_fields=[
                    "iyzico_onboarding_status",
                    "iyzico_onboarding_started_at",
                    "iyzico_last_sync_at",
                    "iyzico_last_error_code",
                    "iyzico_last_error_message",
                ]
            )

        return payload

    @classmethod
    def _is_pending_stale(cls, seller):
        started_at = seller.iyzico_onboarding_started_at

        # Başlangıç zamanı olmayan bir pending satırı eski kabul edilir
        if started_at is None:
            return True

        return (
            timezone.now() - started_at
            > cls.PENDING_TIMEOUT
        )

    # ------------------------------------------------------------------
    # CREATE RESPONSE / AMBIGUOUS RESULT
    # ------------------------------------------------------------------

    @classmethod
    def _resolve_ambiguous_create(
        cls,
        *,
        seller_profile_id,
        payload,
        original_exception,
    ):
        """
        Resolve an ambiguous CREATE request.

        Never blindly CREATE again.

        First retrieve by stable external ID.
        """

        external_id = payload["subMerchantExternalId"]

        lookup = cls._retrieve_remote(
            external_id=external_id,
        )

        # CREATE işlemi başarılı

        if lookup["state"] == "found":
            return cls._mark_active_from_remote(
                seller_profile_id=seller_profile_id,
                remote=lookup["response"],
                require_external_id=True,
            )


        # TEKRAR CREATE YAPMA.

        if lookup["state"] == "unknown":
            cls._mark_reconciliation_required(
                seller_profile_id=seller_profile_id,
                error_code=lookup.get("error_code"),
                error_message=(
                    lookup.get("error_message")
                    or str(original_exception)
                ),
            )

            raise SubmerchantReconciliationRequiredError(
                "iyzico CREATE sonucunun durumu doğrulanamadı. "
                "Mutabakat gerekiyor."
            ) from original_exception


        # yalnızca 1 kere yeniden Create denemesine izin ver

        try:
            retry_response = cls._create_remote(
                payload=payload,
            )

        except (
            IyzicoTransportError,
            IyzicoResponseError,
        ) as retry_exc:
            logger.warning(
                "Second iyzico submerchant CREATE attempt "
                "became ambiguous. seller_profile_id=%s "
                "external_id=%s error=%s",
                seller_profile_id,
                external_id,
                retry_exc,
            )

            retry_lookup = cls._retrieve_remote(
                external_id=external_id,
            )

            if retry_lookup["state"] == "found":
                return cls._mark_active_from_remote(
                    seller_profile_id=seller_profile_id,
                    remote=retry_lookup["response"],
                    require_external_id=True,
                )

            cls._mark_reconciliation_required(
                seller_profile_id=seller_profile_id,
                error_code=retry_lookup.get("error_code"),
                error_message=(
                    retry_lookup.get("error_message")
                    or str(retry_exc)
                ),
            )

            raise SubmerchantReconciliationRequiredError(
                "iyzico submerchant oluşturma işleminin "
                "sonucu doğrulanamadı. Mutabakat gerekiyor."
            ) from retry_exc

        return cls._handle_create_response(
            seller_profile_id=seller_profile_id,
            payload=payload,
            response=retry_response,
        )

    @classmethod
    def _handle_create_response(
        cls,
        *,
        seller_profile_id,
        payload,
        response,
    ):
        status = response.get("status")
        external_id = payload["subMerchantExternalId"]

        # ==============================================================
        # SUCCESS
        # ==============================================================

        if status == "success":
            submerchant_key = cls._clean(
                response.get("subMerchantKey")
            )

            # Key olmadan success olmaz
            if not submerchant_key:
                lookup = cls._retrieve_remote(
                    external_id=external_id,
                )

                if lookup["state"] == "found":
                    return cls._mark_active_from_remote(
                        seller_profile_id=seller_profile_id,
                        remote=lookup["response"],
                        require_external_id=True,
                    )

                cls._mark_reconciliation_required(
                    seller_profile_id=seller_profile_id,
                    error_code=(
                        response.get("errorCode")
                        or lookup.get("error_code")
                        or ""
                    ),
                    error_message=(
                        lookup.get("error_message")
                        or (
                            "iyzico success yanıtında "
                            "subMerchantKey bulunamadı."
                        )
                    ),
                )

                raise SubmerchantReconciliationRequiredError(
                    "iyzico success yanıtında "
                    "subMerchantKey bulunamadı. "
                    "Mutabakat gerekiyor."
                )

            # CREATE işleminin başarılı olması,
            # sabit external ID'mizi ve geçerli bir subMerchantKey içerdiği için durumu
            # active olarak işaretlemek için tek başına yetkilidir.
            return cls._mark_active_from_remote(
                seller_profile_id=seller_profile_id,
                remote=response,
                require_external_id=False,
            )

        # ==============================================================
        # FAILURE
        # ==============================================================

        if status == "failure":
            error_code = cls._normalize_error_code(
                response.get("errorCode")
            )

            error_message = (
                response.get("errorMessage")
                or "iyzico submerchant oluşturulamadı."
            ).strip()

            # ----------------------------------------------------------
            # Duplicate external ID.
            # ----------------------------------------------------------

            if (
                error_code
                == cls.DUPLICATE_EXTERNAL_ID_ERROR_CODE
            ):
                lookup = cls._retrieve_remote(
                    external_id=external_id,
                )

                if lookup["state"] == "found":
                    return cls._mark_active_from_remote(
                        seller_profile_id=seller_profile_id,
                        remote=lookup["response"],
                        require_external_id=True,
                    )

                cls._mark_reconciliation_required(
                    seller_profile_id=seller_profile_id,
                    error_code=(
                        lookup.get("error_code")
                        or error_code
                    ),
                    error_message=(
                        lookup.get("error_message")
                        or error_message
                    ),
                )

                raise SubmerchantReconciliationRequiredError(
                    "iyzico external ID'nin zaten mevcut olduğunu "
                    "bildirdi ancak remote kayıt doğrulanamadı. "
                    "Mutabakat gerekiyor."
                )


            # Bilinen bir API hatası.

            cls._mark_failed(
                seller_profile_id=seller_profile_id,
                error_code=error_code,
                error_message=error_message,
            )

            raise IyzicoGatewayError(
                "iyzico submerchant oluşturulamadı. "
                f"Kod: {error_code or '-'} | "
                f"{error_message}"
            )

        # ==============================================================
        # UNKNOWN STATUS
        # ==============================================================

        lookup = cls._retrieve_remote(
            external_id=external_id,
        )

        if lookup["state"] == "found":
            return cls._mark_active_from_remote(
                seller_profile_id=seller_profile_id,
                remote=lookup["response"],
                require_external_id=True,
            )

        cls._mark_reconciliation_required(
            seller_profile_id=seller_profile_id,
            error_code=(
                lookup.get("error_code")
                or cls._normalize_error_code(
                    response.get("errorCode")
                )
            ),
            error_message=(
                lookup.get("error_message")
                or "iyzico beklenmeyen bir yanıt döndürdü."
            ),
        )

        raise SubmerchantReconciliationRequiredError(
            "iyzico beklenmeyen bir yanıt döndürdü. "
            "Mutabakat gerekiyor."
        )

    # ------------------------------------------------------------------
    # REMOTE CREATE
    # ------------------------------------------------------------------

    @classmethod
    def _create_remote(cls, *, payload):
        """
        Call iyzico outside a database transaction.

        Transport errors are considered ambiguous because the request
        may already have reached iyzico.
        """

        try:
            resource = iyzipay.SubMerchant().create(
                payload,
                cls._options(),
            )
        except Exception as exc:
            raise IyzicoTransportError(
                "iyzico submerchant CREATE isteği "
                "gönderilemedi veya yanıt alınamadı."
            ) from exc

        try:
            return cls._read_json(
                resource
            )
        except IyzicoResponseError:
            raise
        except Exception as exc:
            raise IyzicoResponseError(
                "iyzico submerchant CREATE yanıtı "
                "okunamadı."
            ) from exc

    # ------------------------------------------------------------------
    # REMOTE RETRIEVE
    # ------------------------------------------------------------------

    @classmethod
    def _retrieve_remote(cls, *, external_id):
        """
        Retrieve submerchant using the stable external ID.

        2001:
            remote submerchant not found for this external ID

        anything else:
            unknown unless response is successful.
        """

        conversation_id = uuid.uuid4().hex

        request = {
            "locale": cls.LOCALE,
            "conversationId": conversation_id,
            "subMerchantExternalId": external_id,
        }

        try:
            resource = iyzipay.SubMerchant().retrieve(
                request,
                cls._options(),
            )
        except Exception as exc:
            logger.warning(
                "iyzico submerchant RETRIEVE transport error. "
                "external_id=%s error=%s",
                external_id,
                exc,
            )

            return {
                "state": "unknown",
                "error_code": "",
                "error_message": str(exc),
            }

        try:
            response = cls._read_json(
                resource
            )
        except IyzicoResponseError as exc:
            logger.warning(
                "iyzico submerchant RETRIEVE response error. "
                "external_id=%s error=%s",
                external_id,
                exc,
            )

            return {
                "state": "unknown",
                "error_code": "",
                "error_message": str(exc),
            }

        status = response.get("status")

        # --------------------------------------------------------------
        # FOUND
        # --------------------------------------------------------------

        if status == "success":
            submerchant_key = cls._clean(
                response.get("subMerchantKey")
            )

            if not submerchant_key:
                return {
                    "state": "unknown",
                    "error_code": cls._normalize_error_code(
                        response.get("errorCode")
                    ),
                    "error_message": (
                        "Retrieve success yanıtında "
                        "subMerchantKey bulunamadı."
                    ),
                }

            remote_external_id = cls._clean(
                response.get("subMerchantExternalId")
            )

            # Retrieve yanıtının external ID'yi içermesi beklenir.
            # Bu metot reconciliation amacıyla kullanıldığı için burada
            # external ID'nin bulunması zorunlu
            if not remote_external_id:
                return {
                    "state": "unknown",
                    "error_code": "",
                    "error_message": (
                        "Retrieve response içinde "
                        "subMerchantExternalId bulunamadı."
                    ),
                }

            if remote_external_id != external_id:
                return {
                    "state": "unknown",
                    "error_code": "",
                    "error_message": (
                        "Retrieve response içindeki "
                        "subMerchantExternalId beklenen ID ile "
                        "eşleşmiyor."
                    ),
                }

            return {
                "state": "found",
                "response": response,
            }

        # --------------------------------------------------------------
        # FAILURE
        # --------------------------------------------------------------

        if status == "failure":
            error_code = cls._normalize_error_code(
                response.get("errorCode")
            )

            error_message = (
                response.get("errorMessage")
                or "iyzico retrieve işlemi başarısız."
            ).strip()

            if (
                error_code
                == cls.NOT_FOUND_ERROR_CODE
            ):
                return {
                    "state": "not_found",
                    "error_code": error_code,
                    "error_message": error_message,
                }

            return {
                "state": "unknown",
                "error_code": error_code,
                "error_message": error_message,
            }

        # --------------------------------------------------------------
        # UNKNOWN STATUS
        # --------------------------------------------------------------

        return {
            "state": "unknown",
            "error_code": cls._normalize_error_code(
                response.get("errorCode")
            ),
            "error_message": (
                "iyzico retrieve beklenmeyen "
                "status döndürdü."
            ),
        }

    # ------------------------------------------------------------------
    # DATABASE STATE MANAGEMENT
    # ------------------------------------------------------------------

    @classmethod
    def _mark_active_from_remote(
        cls,
        *,
        seller_profile_id,
        remote,
        require_external_id,
    ):
        """
        Persist a confirmed remote submerchant as ACTIVE.
        """

        submerchant_key = cls._clean(
            remote.get("subMerchantKey")
        )

        if not submerchant_key:
            raise IyzicoResponseError(
                "iyzico subMerchantKey bilgisi alınamadı."
            )

        remote_external_id = cls._clean(
            remote.get("subMerchantExternalId")
        )

        if require_external_id:
            if not remote_external_id:
                raise IyzicoResponseError(
                    "iyzico response içinde "
                    "subMerchantExternalId bulunamadı."
                )

        with transaction.atomic():
            seller = cls._get_seller_locked(
                seller_profile_id=seller_profile_id,
            )

            # CREATE başarılı olduğunda external ID'yi zaten kendi isteğimizden biliyoruz.
            # RETRIEVE işleminde ise external ID'nin mevcut olmasını açıkça zorunlu tutup karşılaştırıyoruz.
            if (
                remote_external_id
                and remote_external_id
                != seller.iyzico_submerchant_external_id
            ):
                raise IyzicoResponseError(
                    "iyzico subMerchantExternalId ile "
                    "yerel SellerProfile eşleşmiyor."
                )

            now = timezone.now()

            seller.iyzico_submerchant_key = (
                submerchant_key
            )
            seller.iyzico_onboarding_status = (
                IyzicoOnboardingStatus.ACTIVE
            )
            seller.iyzico_onboarding_started_at = None
            seller.iyzico_last_sync_at = now
            seller.iyzico_last_error_code = ""
            seller.iyzico_last_error_message = ""

            update_fields = [
                "iyzico_submerchant_key",
                "iyzico_onboarding_status",
                "iyzico_onboarding_started_at",
                "iyzico_last_sync_at",
                "iyzico_last_error_code",
                "iyzico_last_error_message",
            ]

            if seller.iyzico_onboarded_at is None:
                seller.iyzico_onboarded_at = now
                update_fields.append(
                    "iyzico_onboarded_at"
                )

            seller.save(
                update_fields=update_fields,
            )

        return seller

    @classmethod
    def _mark_failed(
        cls,
        *,
        seller_profile_id,
        error_code,
        error_message,
    ):
        with transaction.atomic():
            seller = cls._get_seller_locked(
                seller_profile_id=seller_profile_id,
            )

            seller.iyzico_onboarding_status = (
                IyzicoOnboardingStatus.FAILED
            )
            seller.iyzico_onboarding_started_at = None
            seller.iyzico_last_sync_at = timezone.now()
            seller.iyzico_last_error_code = (
                error_code or ""
            )
            seller.iyzico_last_error_message = (
                error_message or ""
            )[:5000]

            seller.save(
                update_fields=[
                    "iyzico_onboarding_status",
                    "iyzico_onboarding_started_at",
                    "iyzico_last_sync_at",
                    "iyzico_last_error_code",
                    "iyzico_last_error_message",
                ]
            )

        return seller

    @classmethod
    def _mark_reconciliation_required(
        cls,
        *,
        seller_profile_id,
        error_code=None,
        error_message=None,
    ):
        with transaction.atomic():
            seller = cls._get_seller_locked(
                seller_profile_id=seller_profile_id,
            )

            seller.iyzico_onboarding_status = (
                IyzicoOnboardingStatus.RECONCILIATION_REQUIRED
            )
            seller.iyzico_last_sync_at = timezone.now()
            seller.iyzico_last_error_code = (
                error_code or ""
            )
            seller.iyzico_last_error_message = (
                error_message or ""
            )[:5000]

            seller.save(
                update_fields=[
                    "iyzico_onboarding_status",
                    "iyzico_last_sync_at",
                    "iyzico_last_error_code",
                    "iyzico_last_error_message",
                ]
            )

        return seller

    # ------------------------------------------------------------------
    # LOCAL STATE VALIDATION
    # ------------------------------------------------------------------

    @classmethod
    def _validate_local_state(cls, *, seller):
        status = seller.iyzico_onboarding_status
        has_key = bool(
            seller.iyzico_submerchant_key
        )

        # --------------------------------------------------------------
        # ACTIVE
        # --------------------------------------------------------------

        if status == IyzicoOnboardingStatus.ACTIVE:
            if has_key:
                raise SubmerchantAlreadyOnboardedError(
                    "Seller zaten iyzico submerchant "
                    "olarak aktif."
                )

            raise SubmerchantReconciliationRequiredError(
                "Seller ACTIVE durumda ancak "
                "subMerchantKey eksik. "
                "Mutabakat gerekiyor."
            )

        # --------------------------------------------------------------
        # RECONCILIATION REQUIRED
        # --------------------------------------------------------------

        if (
            status
            == IyzicoOnboardingStatus.RECONCILIATION_REQUIRED
        ):
            raise SubmerchantReconciliationRequiredError(
                "Seller'ın iyzico durumu belirsiz. "
                "Önce reconcile yapılmalıdır."
            )

        # --------------------------------------------------------------
        # SUSPENDED
        # --------------------------------------------------------------

        if status == IyzicoOnboardingStatus.SUSPENDED:
            raise SubmerchantError(
                "Seller'ın iyzico submerchant hesabı "
                "askıya alınmış."
            )

        # --------------------------------------------------------------
        # INCONSISTENT LOCAL STATE
        # --------------------------------------------------------------

        if has_key:
            raise SubmerchantReconciliationRequiredError(
                "Seller'da subMerchantKey mevcut ancak "
                "onboarding durumu ACTIVE değil. "
                "Mutabakat gerekiyor."
            )

        # NOT_STARTED ve FAILED durumlarında onboarding işleminin yeniden başlatılmasına izin verilir.

    # ------------------------------------------------------------------
    # PAYLOAD
    # ------------------------------------------------------------------

    @classmethod
    def _get_identity_number_for_create(cls, seller):
        """
        Submerchant oluştururken kullanılacak kimlik numarasını belirler.

        Production:
            SellerProfile.identity_number zorunludur.

        Sandbox:
            SellerProfile.identity_number boşsa
            IYZICO_SANDBOX_IDENTITY_NUMBER fallback olarak kullanılır.

        Gerçek identity_number mevcutsa Sandbox'ta da öncelik ondadır.
        """
        identity_number = cls._clean(
            getattr(seller, "identity_number", "")
        )

        if identity_number:
            return identity_number

        environment = str(
            getattr(settings, "IYZICO_ENV", "") or ""
        ).strip().lower()

        if environment == "sandbox":
            identity_number = cls._clean(
                getattr(
                    settings,
                    "IYZICO_SANDBOX_IDENTITY_NUMBER",
                    "",
                )
            )

            if identity_number:
                return identity_number

        raise SubmerchantValidationError(
            "Kimlik numarası zorunludur."
        )

    @classmethod
    def _build_create_payload(
        cls,
        *,
        seller,
        conversation_id,
    ):
        """
        Build seller-type-specific iyzico payload.

        PERSONAL:
            contactName
            contactSurname
            identityNumber

        PRIVATE_COMPANY:
            taxOffice
            legalCompanyTitle
            contactName
            contactSurname
            identityNumber

        LIMITED_OR_JOINT_STOCK_COMPANY:
            taxOffice
            legalCompanyTitle
            taxNumber
        """

        seller_type = cls._normalize_seller_type(
            seller.seller_type
        )

        # --------------------------------------------------------------
        # Stable external ID
        # --------------------------------------------------------------

        external_id = cls._clean(
            seller.iyzico_submerchant_external_id
        )

        cls._validate_external_id(
            external_id=external_id,
        )

        # --------------------------------------------------------------
        # User/contact data
        # --------------------------------------------------------------

        user = seller.user

        first_name = cls._clean(
            user.first_name
        )

        last_name = cls._clean(
            user.last_name
        )

        email = cls._clean(
            user.email
        )

        phone = cls._normalize_phone(
            seller.company_phone
            or user.phone_number
        )

        address = cls._clean(
            seller.company_address
        )

        iban = cls._clean(
            seller.iban
        ).replace(" ", "").upper()

        # --------------------------------------------------------------
        # Common validation
        # --------------------------------------------------------------

        cls._require_length(
            value=address,
            field="company_address",
            min_length=cls.ADDRESS_MIN_LENGTH,
            max_length=cls.ADDRESS_MAX_LENGTH,
        )

        cls._require_email(email)
        cls._require_phone(phone)
        cls._require_iban(iban)

        # --------------------------------------------------------------
        # Name
        # --------------------------------------------------------------

        if seller_type == SellerType.PERSONAL:
            name = (
                cls._clean(seller.company_name)
                or f"{first_name} {last_name}".strip()
            )

        else:
            name = (
                cls._clean(seller.company_name)
                or cls._clean(
                    seller.legal_company_title
                )
            )

            if not name:
                raise SubmerchantValidationError(
                    "Şirket submerchant için "
                    "company_name veya "
                    "legal_company_title zorunludur."
                )

        cls._require_length(
            value=name,
            field="name",
            min_length=cls.NAME_MIN_LENGTH,
            max_length=cls.NAME_MAX_LENGTH,
        )

        # --------------------------------------------------------------
        # Common payload
        # --------------------------------------------------------------

        payload = {
            "locale": cls.LOCALE,
            "conversationId": conversation_id,
            "subMerchantExternalId": external_id,
            "subMerchantType": seller_type.value,
            "address": address,
            "email": email,
            "gsmNumber": phone,
            "name": name,
            "iban": iban,
            "currency": cls.CURRENCY,
        }

        # --------------------------------------------------------------
        # PERSONAL
        # --------------------------------------------------------------

        if seller_type == SellerType.PERSONAL:
            cls._validate_contact_information(
                first_name=first_name,
                last_name=last_name,
            )

            identity_number = cls._get_identity_number_for_create(
                seller
            )
            
            cls._require_identity_number(
                identity_number
            )

            payload.update(
                {
                    "contactName": first_name,
                    "contactSurname": last_name,
                    "identityNumber": identity_number,
                }
            )

        # --------------------------------------------------------------
        # PRIVATE COMPANY
        # --------------------------------------------------------------

        elif seller_type == SellerType.PRIVATE_COMPANY:
            cls._validate_contact_information(
                first_name=first_name,
                last_name=last_name,
            )

            tax_office = cls._clean(
                seller.tax_office
            )

            legal_company_title = cls._clean(
                seller.legal_company_title
            )

            identity_number = cls._clean(
                seller.identity_number
            )

            cls._require_length(
                value=tax_office,
                field="tax_office",
                min_length=cls.TAX_OFFICE_MIN_LENGTH,
                max_length=cls.TAX_OFFICE_MAX_LENGTH,
            )

            cls._require_length(
                value=legal_company_title,
                field="legal_company_title",
                min_length=(
                    cls.LEGAL_COMPANY_TITLE_MIN_LENGTH
                ),
                max_length=(
                    cls.LEGAL_COMPANY_TITLE_MAX_LENGTH
                ),
            )

            cls._require_identity_number(
                identity_number
            )

            payload.update(
                {
                    "taxOffice": tax_office,
                    "legalCompanyTitle": (
                        legal_company_title
                    ),
                    "contactName": first_name,
                    "contactSurname": last_name,
                    "identityNumber": identity_number,
                }
            )

        # --------------------------------------------------------------
        # LIMITED / JOINT STOCK COMPANY
        # --------------------------------------------------------------

        elif (
            seller_type
            == SellerType.LIMITED_OR_JOINT_STOCK_COMPANY
        ):
            tax_office = cls._clean(
                seller.tax_office
            )

            legal_company_title = cls._clean(
                seller.legal_company_title
            )

            tax_number = cls._clean(
                seller.tax_number
            )

            cls._require_length(
                value=tax_office,
                field="tax_office",
                min_length=cls.TAX_OFFICE_MIN_LENGTH,
                max_length=cls.TAX_OFFICE_MAX_LENGTH,
            )

            cls._require_length(
                value=legal_company_title,
                field="legal_company_title",
                min_length=(
                    cls.LEGAL_COMPANY_TITLE_MIN_LENGTH
                ),
                max_length=(
                    cls.LEGAL_COMPANY_TITLE_MAX_LENGTH
                ),
            )

            cls._require_length(
                value=tax_number,
                field="tax_number",
                min_length=cls.TAX_NUMBER_MIN_LENGTH,
                max_length=cls.TAX_NUMBER_MAX_LENGTH,
            )

            payload.update(
                {
                    "taxOffice": tax_office,
                    "legalCompanyTitle": (
                        legal_company_title
                    ),
                    "taxNumber": tax_number,
                }
            )

        else:
            raise SubmerchantValidationError(
                "Geçersiz seller tipi."
            )

        return payload

    # ------------------------------------------------------------------
    # VALIDATION HELPERS
    # ------------------------------------------------------------------

    @staticmethod
    def _normalize_seller_type(value):
        if isinstance(value, SellerType):
            return value

        try:
            return SellerType(value)
        except (ValueError, TypeError) as exc:
            raise SubmerchantValidationError(
                "Geçersiz seller tipi."
            ) from exc

    @staticmethod
    def _clean(value):
        return str(value or "").strip()

    @classmethod
    def _validate_external_id(cls, *, external_id):
        cls._require_length(
            value=external_id,
            field="iyzico_submerchant_external_id",
            min_length=cls.EXTERNAL_ID_MIN_LENGTH,
            max_length=cls.EXTERNAL_ID_MAX_LENGTH,
        )

        # Bu formatta oluşturulan değerle eşleşmelidir:
        # seller-{uuid.uuid4().hex}
        
        if not re.fullmatch(
            r"seller-[0-9a-f]{32}",
            external_id,
        ):
            raise SubmerchantValidationError(
                "Geçersiz iyzico submerchant external ID."
            )

    @classmethod
    def _validate_contact_information(
        cls,
        *,
        first_name,
        last_name,
    ):
        cls._require_length(
            value=first_name,
            field="first_name",
            min_length=cls.CONTACT_NAME_MIN_LENGTH,
            max_length=cls.CONTACT_NAME_MAX_LENGTH,
        )

        cls._require_length(
            value=last_name,
            field="last_name",
            min_length=cls.CONTACT_SURNAME_MIN_LENGTH,
            max_length=cls.CONTACT_SURNAME_MAX_LENGTH,
        )

    @staticmethod
    def _require_email(value):
        if not value:
            raise SubmerchantValidationError(
                "Email zorunludur."
            )

        try:
            validate_email(value)
        except ValidationError as exc:
            raise SubmerchantValidationError(
                "Geçerli bir email adresi girilmelidir."
            ) from exc

    @staticmethod
    def _normalize_phone(value):
        """
        Normalize common Turkish GSM formats to +90XXXXXXXXXX.
        """

        raw = str(value or "").strip()

        if not raw:
            return ""

        digits = re.sub(
            r"\D",
            "",
            raw,
        )

        # 905xxxxxxxxx
        if (
            digits.startswith("90")
            and len(digits) == 12
        ):
            return f"+{digits}"

        # 05xxxxxxxxx
        if (
            digits.startswith("0")
            and len(digits) == 11
        ):
            return f"+90{digits[1:]}"

        # 5xxxxxxxxx
        if len(digits) == 10:
            return f"+90{digits}"

        return raw

    @classmethod
    def _require_phone(cls, value):
        if not value:
            raise SubmerchantValidationError(
                "Telefon numarası zorunludur."
            )

        if len(value) > cls.PHONE_MAX_LENGTH:
            raise SubmerchantValidationError(
                "Telefon numarası çok uzun."
            )

        if not re.fullmatch(
            r"\+905\d{9}",
            value,
        ):
            raise SubmerchantValidationError(
                "Geçerli bir Türkiye GSM numarası "
                "girilmelidir."
            )

    @staticmethod
    def _require_identity_number(value):
        if not value:
            raise SubmerchantValidationError(
                "Kimlik numarası zorunludur."
            )

        if not re.fullmatch(
            r"\d{11}",
            value,
        ):
            raise SubmerchantValidationError(
                "Kimlik numarası 11 haneli "
                "olmalıdır."
            )

    @staticmethod
    def _require_iban(value):
        """
        Validate Turkish IBAN format and MOD-97 checksum.
        """

        if not value:
            raise SubmerchantValidationError(
                "IBAN zorunludur."
            )

        if not re.fullmatch(
            r"TR\d{24}",
            value,
        ):
            raise SubmerchantValidationError(
                "Geçerli bir Türkiye IBAN'ı girilmelidir."
            )

        # IBAN:
        # ilk dört karakteri sona taşı.
        rearranged = value[4:] + value[:4]

        numeric = ""

        for char in rearranged:
            if char.isdigit():
                numeric += char
            else:
                numeric += str(
                    ord(char) - ord("A") + 10
                )

        if int(numeric) % 97 != 1:
            raise SubmerchantValidationError(
                "IBAN checksum doğrulaması başarısız."
            )

    @staticmethod
    def _require_length(
        *,
        value,
        field,
        min_length,
        max_length,
    ):
        if not value:
            raise SubmerchantValidationError(
                f"{field} zorunludur."
            )

        length = len(value)

        if length < min_length:
            raise SubmerchantValidationError(
                f"{field} en az "
                f"{min_length} karakter olmalıdır."
            )

        if length > max_length:
            raise SubmerchantValidationError(
                f"{field} en fazla "
                f"{max_length} karakter olabilir."
            )

    @staticmethod
    def _normalize_error_code(value):
        if value in (None, ""):
            return ""

        return str(value)

    # ------------------------------------------------------------------
    # JSON / SETTINGS
    # ------------------------------------------------------------------

    @staticmethod
    def _read_json(resource):
        try:
            raw = resource.read()

            if isinstance(raw, bytes):
                raw = raw.decode("utf-8")

            if not raw:
                raise IyzicoResponseError(
                    "iyzico boş response döndürdü."
                )

            return json.loads(raw)

        except IyzicoResponseError:
            raise

        except (
            UnicodeDecodeError,
            TypeError,
            ValueError,
        ) as exc:
            raise IyzicoResponseError(
                "iyzico response JSON formatında değil."
            ) from exc

    @staticmethod
    def _options():
        return {
            "api_key": settings.IYZICO_API_KEY,
            "secret_key": settings.IYZICO_SECRET_KEY,
            "base_url": settings.IYZICO_BASE_URL,
        }

    @staticmethod
    def _validate_settings():
        required_settings = (
            "IYZICO_API_KEY",
            "IYZICO_SECRET_KEY",
            "IYZICO_BASE_URL",
        )

        missing = [
            setting_name
            for setting_name in required_settings
            if not getattr(
                settings,
                setting_name,
                None,
            )
        ]

        if missing:
            raise IyzicoGatewayError(
                "Iyzico ayarları eksik: "
                + ", ".join(missing)
            )

    # ------------------------------------------------------------------
    # DATABASE HELPERS
    # ------------------------------------------------------------------

    @classmethod
    def _get_seller_locked(
        cls,
        *,
        seller_profile_id,
    ):
        try:
            return (
                SellerProfile.objects
                .select_for_update()
                .select_related("user")
                .get(pk=seller_profile_id)
            )

        except ObjectDoesNotExist as exc:
            raise SubmerchantError(
                "Seller profili bulunamadı."
            ) from exc

    @classmethod
    def _get_seller_for_reconciliation(
        cls,
        *,
        seller_profile_id,
    ):
        try:
            return (
                SellerProfile.objects
                .select_related("user")
                .get(pk=seller_profile_id)
            )

        except ObjectDoesNotExist as exc:
            raise SubmerchantError(
                "Seller profili bulunamadı."
            ) from exc