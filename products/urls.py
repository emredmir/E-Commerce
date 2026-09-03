from django.urls import path


from products.views.wizard import(
    # Satıcı dashboard view'ları
    ProductWizardStep1View,
    BrandRequestCreateView,
    MatchDecisionView,
)

from products.views.wizard_step2 import ProductWizardStep2CompleteView, ProductWizardStep2View, VariantCreateView, VariantDeleteView, BulkVariantCreateView, VariantDeleteAllView

from products.views.wizard_step3 import (
    ProductWizardStep3View,
    ImageGroupCreateView,
    ImageGroupUpdateView,
    ImageGroupDeleteView,
    ImageUploadView,
    ImageUpdateView,
    ImageDeleteView,
    ImageReorderView,
    ProductWizardStep3CompleteView,
)

from products.views.wizard_step4 import ProductWizardStep4View, ProductWizardStep4SaveView, ProductWizardStep4CompleteView

from products.views.wizard_step5 import ProductWizardStep5View, ProductWizardPublishView

from products.views.api import (
    CategoryBrandAPIView,
    CategoryChildrenAPIView,
    CollectionListAPIView,
    CollectionCreateAPIView,
    CollectionToggleAPIView,
    ProductQAAskAPIView,
    ProductQAUpvoteAPIView,
    ProductQAAnswerDeleteAPIView,
    ProductQAListAPIView,
)

from products.views.offer import OfferCreateView, OfferCustomVariantCreateView, CategoryAttributesAPIView

from products.views.inventory import StoreProductListView, StoreProductUpdateView, ProductUpdateView, StoreProductArchiveView

from products.views.storefront import ProductListView, CategoryProductListView, ProductDetailView

app_name = 'products'

urlpatterns = [
    # products/urls.py
    # Müşteri Vitrini
    path('', ProductListView.as_view(), name='product_list'),
    path('category/<slug:slug>/', CategoryProductListView.as_view(), name='category_product_list'),
    

    # Satıcı Dashboard
    # path('store/<slug:store_slug>/products/', StoreProductListView.as_view(), name='store_product_list'),

    # path('store/<slug:store_slug>/products/search/', ProductSearchView.as_view(), name='product_search'),



    # path('store/<slug:store_slug>/products/offer/<int:pk>/update/', StoreProductUpdateView.as_view(), name='offer_update'),

    # path('store/<slug:store_slug>/products/<slug:product_slug>/edit/', ProductUpdateView.as_view(), name='product_update',),

    # path('store/<slug:store_slug>/products/offer/<int:pk>/archive/', StoreProductArchiveView.as_view(), name='offer_archive'),







    path('seller/<slug:store_slug>/inventory/', StoreProductListView.as_view(), name='store_product_list'),

    path('seller/<slug:store_slug>/inventory/offer/<int:pk>/update/', StoreProductUpdateView.as_view(), name='offer_update',),

    path('seller/<slug:store_slug>/inventory/<slug:product_slug>/edit/', ProductUpdateView.as_view(), name='product_update',),

    path('seller/<slug:store_slug>/inventory/offer/<int:pk>/archive/', StoreProductArchiveView.as_view(), name='offer_archive'),

    path('<slug:slug>/', ProductDetailView.as_view(), name='product_detail'),

    # Koleksiyon API URL'leri
    path('api/collections/', CollectionListAPIView.as_view(), name='api_collections_list'),
    path('api/collections/toggle/', CollectionToggleAPIView.as_view(), name='api_collections_toggle'),
    path('api/collections/create/', CollectionCreateAPIView.as_view(), name='api_collections_create'),

    # Q&A (Soru-Cevap) API Uçları
    path('api/qa/ask/', ProductQAAskAPIView.as_view(), name='api_qa_ask'),
    path('api/qa/list/<int:product_id>/', ProductQAListAPIView.as_view(), name='api_qa_list'),
    path('api/qa/upvote/', ProductQAUpvoteAPIView.as_view(), name='api_qa_upvote'),
    path("api/qa/answers/<int:answer_id>/delete/", ProductQAAnswerDeleteAPIView.as_view(), name="api_qa_answer_delete"),

    #----------------------- yeni wizard
    # ==========================================================
    # Product Wizard
    # ==========================================================
    #Wizard Step 1
    path("seller/<slug:store_slug>/wizard/", ProductWizardStep1View.as_view(), name="wizard_step1"),
    path("seller/<slug:store_slug>/wizard/<int:draft_id>/", ProductWizardStep1View.as_view(), name="wizard_step1_edit",),

    # Wizard Step 2
    path("seller/<slug:store_slug>/wizard/<int:draft_id>/variants/", ProductWizardStep2View.as_view(), name="wizard_step2",),
    path("seller/<slug:store_slug>/wizard/<int:draft_id>/step2/complete/", ProductWizardStep2CompleteView.as_view(), name="wizard_step2_complete",),

    # Wizard Step 3
    path("seller/<slug:store_slug>/wizard/<int:draft_id>/images/", ProductWizardStep3View.as_view(), name="wizard_step3",),
    path("seller/<slug:store_slug>/wizard/<int:draft_id>/step3/complete/", ProductWizardStep3CompleteView.as_view(), name="wizard_step3_complete",),

    # Wizard Step 4
    path("seller/<slug:store_slug>/wizard/<int:draft_id>/offers/", ProductWizardStep4View.as_view(), name="wizard_step4",),
    path("seller/<slug:store_slug>/wizard/<int:draft_id>/step4/complete/", ProductWizardStep4CompleteView.as_view(), name="wizard_step4_complete",),

    # Wizard Step 5
    path("seller/<slug:store_slug>/wizard/<int:draft_id>/review/", ProductWizardStep5View.as_view(), name="wizard_step5",),

    # ==========================================================
    # Offer
    # ==========================================================

    path("seller/<slug:store_slug>/offer/<int:draft_id>/", OfferCreateView.as_view(), name="offer_create",),

    path("api/<slug:store_slug>/offer/<int:draft_id>/variant/create/", OfferCustomVariantCreateView.as_view(), name="offer_custom_variant_create",),
    path("api/categories/<int:category_id>/attributes/",  CategoryAttributesAPIView.as_view(), name="api_category_attributes"),


    # ==========================================================
    # Wizard APIs
    # ==========================================================
    path("api/categories/<int:parent_id>/children/", CategoryChildrenAPIView.as_view(), name="api_category_children",),
    path("api/categories/<int:category_id>/brands/", CategoryBrandAPIView.as_view(), name="api_category_brands",),
    path("api/brand-requests/<int:category_id>/", BrandRequestCreateView.as_view(), name="api_brand_request",),
    path("api/<slug:store_slug>/drafts/<int:draft_id>/match/", MatchDecisionView.as_view(), name="draft_match_decision",),


    path("api/<slug:store_slug>/wizard/<int:draft_id>/variants/create/", VariantCreateView.as_view(), name="variant_create",),
    path("api/<slug:store_slug>/wizard/<int:draft_id>/variants/bulk/", BulkVariantCreateView.as_view(), name="bulk_variant_create",),
    path("api/<slug:store_slug>/wizard/<int:draft_id>/variants/<int:variant_id>/delete/", VariantDeleteView.as_view(), name="variant_delete",),
    path("api/<slug:store_slug>/drafts/<int:draft_id>/variants/delete-all/", VariantDeleteAllView.as_view(), name="variant_delete_all",),

    # Wizard Step 3 APIs
    path("api/<slug:store_slug>/wizard/<int:draft_id>/image-groups/create/", ImageGroupCreateView.as_view(), name="image_group_create"),
    path("api/<slug:store_slug>/wizard/<int:draft_id>/image-groups/<int:group_id>/update/", ImageGroupUpdateView.as_view(), name="image_group_update"),
    path("api/<slug:store_slug>/wizard/<int:draft_id>/image-groups/<int:group_id>/delete/", ImageGroupDeleteView.as_view(), name="image_group_delete"),
    path("api/<slug:store_slug>/wizard/<int:draft_id>/image-groups/<int:group_id>/upload/", ImageUploadView.as_view(), name="image_upload"),
    path("api/<slug:store_slug>/wizard/<int:draft_id>/images/<int:image_id>/update/", ImageUpdateView.as_view(), name="image_update"),
    path("api/<slug:store_slug>/wizard/<int:draft_id>/images/<int:image_id>/delete/", ImageDeleteView.as_view(), name="image_delete"),
    path("api/<slug:store_slug>/wizard/<int:draft_id>/image-groups/<int:group_id>/reorder/", ImageReorderView.as_view(), name="image_reorder"),

    path("api/<slug:store_slug>/wizard/<int:draft_id>/offers/save/", ProductWizardStep4SaveView.as_view(), name="wizard_step4_save",),

    path("api/<slug:store_slug>/wizard/<int:draft_id>/publish/", ProductWizardPublishView.as_view(), name="wizard_publish",),

    



]
