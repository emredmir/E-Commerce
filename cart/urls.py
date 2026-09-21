from django.urls import path
from . import views

app_name = 'cart'

urlpatterns = [
    # Cart Sayfası (HTML)
    path('', views.CartDetailView.as_view(), name='detail'),
    
    # Cart JSON Verisi (Mini-cart veya frontend güncellemeleri için)
    path('api/data/', views.CartDataAPIView.as_view(), name='cart_data'),

    # Sepete Ekleme (item_id body'den geliyor, URL'ye gerek yok)
    path('api/add/', views.AddToCartAPIView.as_view(), name='add_to_cart'),
    
    # Adet Güncelleme (Hangi ürün olduğu URL'den geliyor: <int:item_id>)
    path('api/update/<int:item_id>/', views.UpdateCartItemQuantityAPIView.as_view(), name='update_quantity'),
    
    # Ürün Silme (Hangi ürün olduğu URL'den geliyor: <int:item_id>)
    path('api/remove/<int:item_id>/', views.RemoveCartItemAPIView.as_view(), name='remove_item'),
    
    # Seçim Değiştirme (Hangi ürün olduğu URL'den geliyor: <int:item_id>)
    path('api/toggle-selection/<int:item_id>/', views.SetCartItemSelectionAPIView.as_view(), name='toggle_selection'),
    
    # Fiyat Değişikliği Onayı
    path('api/acknowledge-price/', views.AcknowledgePriceChangesAPIView.as_view(), name='acknowledge_price'),
    
    # Checkout Öncesi Doğrulama
    path('api/validate-checkout/', views.CheckoutValidationAPIView.as_view(), name='validate_checkout'),
]