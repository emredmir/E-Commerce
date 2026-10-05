from django.urls import path

from .views import (
    CheckoutPageView, CheckoutCreateOrderAPIView, CheckoutPaymentAPIView,
    OrderSuccessView,
    StoredCardListCreateAPIView, StoredCardDeleteAPIView, StoredCardDefaultAPIView,
    IyzicoPaymentWebhookAPIView
)

app_name = "orders"

urlpatterns = [
    path("checkout/", CheckoutPageView.as_view(), name="checkout"),
    path("checkout/create/", CheckoutCreateOrderAPIView.as_view(), name="checkout_create"),
    path("checkout/<str:order_number>/payment/", CheckoutPaymentAPIView.as_view(), name="checkout_payment",),
    path("checkout/<str:order_number>/success/", OrderSuccessView.as_view(), name="order_success",),

    path("payment/cards/", StoredCardListCreateAPIView.as_view(), name="stored-card-list-create",),
    path("payment/cards/<int:card_id>", StoredCardDeleteAPIView.as_view(), name="stored-card-delete",),
    path("payment/cards/<int:card_id>/default/", StoredCardDefaultAPIView.as_view(), name="stored-card-default",),

    path("payment/webhook/iyzico/", IyzicoPaymentWebhookAPIView.as_view(), name="iyzico_payment_webhook",),
    

]