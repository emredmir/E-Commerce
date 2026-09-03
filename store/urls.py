from django.urls import path
from .views import (
    StoreCreateView, MyStoresListView, StoreUpdateView, 
    StoreDashboardView, StorePublicDetailView, StoreArchiveView,
    StoreQuestionsListView, StoreAnswerQuestionAPIView
    )
from products.views.api import ProductQAAnswerDeleteAPIView


app_name = 'store'

urlpatterns = [
    # Mağaza oluştur
    path('create-store/', StoreCreateView.as_view(), name='create_store'),

    # Kullanıcının mağazaları
    path('my-stores/', MyStoresListView.as_view(), name='store_list'),

    # Mağaza güncelle
    path('<slug:slug>/update/', StoreUpdateView.as_view(), name='update_store'),

    # Mağaza Silme (Arşiv)
    path('<slug:slug>/archive/', StoreArchiveView.as_view(), name='archive_store'),

    # Mağaza dashboard/detail
    path('<slug:slug>/dashboard/', StoreDashboardView.as_view(), name='store_dashboard'),

    # PUBLIC VİTRİN
    path('<slug:slug>/', StorePublicDetailView.as_view(), name='store_detail'),

    # Soru ve Cevaplar (Satıcı Paneli)
    path('<slug:slug>/questions/', StoreQuestionsListView.as_view(), name='store_questions'),
    path('<slug:slug>/questions/<int:question_id>/answer/', StoreAnswerQuestionAPIView.as_view(), name='api_store_answer_question'),
    path('api/qa/answer/<int:answer_id>/delete/', ProductQAAnswerDeleteAPIView.as_view(), name='api_qa_answer_delete'),
]
