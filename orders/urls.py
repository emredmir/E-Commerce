from django.urls import path

from .views import CheckoutPageView, CheckoutCreateOrderAPIView

app_name = "orders"

urlpatterns = [
    path("checkout/", CheckoutPageView.as_view(), name="checkout"),
    path("checkout/create/", CheckoutCreateOrderAPIView.as_view(), name="checkout_create"),
]