from django.urls import path
from .views import register_view, login_view, logout_view, profile_view, CustomPasswordChangeView, ProfileUpdateView, AddressListView, AddressFormView, AddressDeleteView, BecomeASellerView

app_name = 'accounts'

urlpatterns = [
    path('register/', register_view, name='register'),
    path('login/', login_view, name='login'),
    path('logout/', logout_view, name='logout'),
    path('profile/', profile_view, name='profile'),
    path('profile/password-change', CustomPasswordChangeView.as_view(), name='password_change'),
    path('profile/profile-update/', ProfileUpdateView.as_view(), name='profile_update'),
    path('addresses/', AddressListView.as_view(), name='address_list'),
    path('addresses/form/', AddressFormView.as_view(), name='address_form'),
    path('addresses/form/<int:pk>/', AddressFormView.as_view(), name='address_form_update'),
    path('addresses/delete/<int:pk>/', AddressDeleteView.as_view(), name='address_delete'),
    path('profile/become-a-seller/', BecomeASellerView.as_view(), name='seller_form'),
]
