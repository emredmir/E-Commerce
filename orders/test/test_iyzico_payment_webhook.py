import hashlib
import hmac
from unittest.mock import patch

from django.test import SimpleTestCase, override_settings
from django.test.client import RequestFactory

from orders.exceptions import (
    PaymentGatewayError,
    PaymentValidationError,
    PaymentVerificationError,
)
from orders.services.payment import PaymentService
from orders.views import IyzicoPaymentWebhookAPIView


class WebhookSignatureTests(SimpleTestCase):
    @override_settings(IYZICO_SECRET_KEY="test-secret")
    def test_valid_direct_format_signature(self):
        event_type = "THREE_DS_AUTH"
        payment_id = "28157248"
        conversation_id = "conversation-123"
        status = "SUCCESS"

        message = (
            "test-secret"
            + event_type
            + payment_id
            + conversation_id
            + status
        )

        signature = hmac.new(
            b"test-secret",
            message.encode("utf-8"),
            hashlib.sha256,
        ).hexdigest()

        PaymentService._validate_webhook_signature(
            event_type=event_type,
            payment_id=payment_id,
            payment_conversation_id=conversation_id,
            status=status,
            signature=signature,
        )

    @override_settings(IYZICO_SECRET_KEY="test-secret")
    def test_invalid_signature_is_rejected(self):
        with self.assertRaises(PaymentVerificationError):
            PaymentService._validate_webhook_signature(
                event_type="THREE_DS_AUTH",
                payment_id="28157248",
                payment_conversation_id="conversation-123",
                status="SUCCESS",
                signature="invalid-signature",
            )


class WebhookViewTests(SimpleTestCase):
    def setUp(self):
        self.factory = RequestFactory()
        self.payload = {
            "paymentConversationId": "conversation-123",
            "merchantId": 3404590,
            "paymentId": 28157248,
            "status": "SUCCESS",
            "iyziReferenceCode": "reference-123",
            "iyziEventType": "THREE_DS_AUTH",
            "iyziEventTime": 1766730778396,
            "iyziPaymentId": 28157248,
        }

    @patch.object(PaymentService, "handle_webhook")
    def test_successful_webhook_returns_200(self, handle_webhook):
        handle_webhook.return_value = {
            "handled": True,
            "payment_transaction_id": 1,
            "status": "SUCCESS",
        }

        import json

        request = self.factory.post(
            "/orders/payment/webhook/iyzico/",
            data=json.dumps(self.payload),
            content_type="application/json",
            HTTP_X_IYZ_SIGNATURE_V3="valid",
        )

        response = IyzicoPaymentWebhookAPIView.as_view()(request)

        self.assertEqual(response.status_code, 200)
        handle_webhook.assert_called_once()

    @patch.object(
        PaymentService,
        "handle_webhook",
        side_effect=PaymentVerificationError("invalid signature"),
    )
    def test_verification_error_returns_400(self, handle_webhook):
        import json

        request = self.factory.post(
            "/orders/payment/webhook/iyzico/",
            data=json.dumps(self.payload),
            content_type="application/json",
            HTTP_X_IYZ_SIGNATURE_V3="invalid",
        )

        response = IyzicoPaymentWebhookAPIView.as_view()(request)

        self.assertEqual(response.status_code, 400)

    @patch.object(
        PaymentService,
        "handle_webhook",
        side_effect=PaymentGatewayError("temporary provider failure"),
    )
    def test_provider_error_returns_502(self, handle_webhook):
        import json

        request = self.factory.post(
            "/orders/payment/webhook/iyzico/",
            data=json.dumps(self.payload),
            content_type="application/json",
            HTTP_X_IYZ_SIGNATURE_V3="valid",
        )

        response = IyzicoPaymentWebhookAPIView.as_view()(request)

        self.assertEqual(response.status_code, 502)


class WebhookPayloadParsingTests(SimpleTestCase):
    def test_invalid_json_returns_400(self):
        request = RequestFactory().post(
            "/orders/payment/webhook/iyzico/",
            data="{not-json",
            content_type="application/json",
            HTTP_X_IYZ_SIGNATURE_V3="invalid",
        )

        response = IyzicoPaymentWebhookAPIView.as_view()(request)

        self.assertEqual(response.status_code, 400)

    def test_payload_size_limit_returns_413(self):
        request = RequestFactory().post(
            "/orders/payment/webhook/iyzico/",
            data="{}",
            content_type="application/json",
            CONTENT_LENGTH=str(
                IyzicoPaymentWebhookAPIView.MAX_BODY_BYTES + 1
            ),
        )

        response = IyzicoPaymentWebhookAPIView.as_view()(request)

        self.assertEqual(response.status_code, 413)
