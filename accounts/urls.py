from django.urls import path
from .views import (
    register_view, login_view, logout_view, profile_view, 
    CustomPasswordChangeView, ProfileUpdateView, AddressListView, AddressFormView, AddressDeleteView, BecomeASellerView,
    CollectionListView, CollectionDetailView, CollectionDeleteAPIView, UserQuestionsListView, MarkAnswerAsReadAPIView,
    ProductQADeleteAPIView,
    StoredCardListView, StoredCardCreateAPIView, StoredCardDeleteAPIView, StoredCardDefaultAPIView,
    CustomerOrderListView, CustomerOrderDetailView
    )
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

    #order
    path('orders/', CustomerOrderListView.as_view(), name="order_list",),
    path('orders/<str:order_number>/', CustomerOrderDetailView.as_view(), name="order_detail",),

    #card
    path('stored-cards/', StoredCardListView.as_view(), name="stored_card_list",),
    path('stored-cards/create/', StoredCardCreateAPIView.as_view(), name="stored_card_create",),
    path('stored-cards/<int:card_id>/delete/', StoredCardDeleteAPIView.as_view(), name="stored_card_delete",),
    path('stored-cards/<int:card_id>/default/', StoredCardDefaultAPIView.as_view(), name="stored_card_default",),

    path('collections/', CollectionListView.as_view(), name='collection_list'),
    path('collections/<int:pk>/', CollectionDetailView.as_view(), name='collection_detail'),
    path('api/collections/delete/', CollectionDeleteAPIView.as_view(), name='api_collections_delete'),

    # Soru ve Taleplerim (Hesabım)
    path('questions/', UserQuestionsListView.as_view(), name='user_questions'),
    
    # Okundu İşaretleme API'si
    path('questions/<int:question_id>/read/', MarkAnswerAsReadAPIView.as_view(), name='api_mark_answer_read'),

    path('api/qa/question/<int:question_id>/delete/', ProductQADeleteAPIView.as_view(), name='api_qa_question_delete'),
]
