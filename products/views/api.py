import json
from django.http import JsonResponse
from django.views import View
from django.shortcuts import get_object_or_404
from django.db import transaction
from django.template.loader import render_to_string

from products.models import Category, Brand, ProductCollection, ProductCollectionItem, ProductVariant, Product, ProductQuestion, ProductAnswer, ProductStatus

from products.services.storefront import ProductQAService


class CategoryChildrenAPIView(View):

    def get(self, request, parent_id):

        categories = (
            Category.objects.filter(
                parent_id=parent_id,
                is_active=True,
            )
            .order_by("name")
            .values(
                "id",
                "name",
            )
        )

        return JsonResponse(
            {
                "results": list(categories),
            }
        )
    

class CategoryBrandAPIView(View):

    def get(self, request, category_id):

        brands = (
            Brand.objects.filter(
                brand_categories__category_id=category_id,
                is_active=True,
            )
            .distinct()
            .order_by("name")
            .values(
                "id",
                "name",
            )
        )

        return JsonResponse(
            {
                "results": list(brands),
            }
        )

class CollectionListAPIView(View):
    """
    Kullanıcının sahip olduğu tüm listeleri ve 
    ilgili varyantın bu listelerde olup olmadığını JSON olarak döner.
    """
    def get(self, request, *args, **kwargs):
        # 1. GÜVENLİK (Login değilse yönlendirme yapma, JSON 403 dön!)
        if not request.user.is_authenticated:
            return JsonResponse({'success': False, 'error': 'Giriş yapmanız gerekiyor.'}, status=403)
        
        variant_id = request.GET.get('variant_id')
        
        # 2. Kullanıcının "Varsayılan (Favorilerim)" listesi yoksa sessizce oluştur.
        # Bu sayede sinyallere (signals) gerek kalmadan her kullanıcının bir ana listesi olmasını garantileriz.
        ProductCollection.objects.get_or_create(
            user=request.user,
            is_default=True,
            defaults={'name': 'Favorilerim'}
        )
        
        # 3. Kullanıcının tüm listelerini çek
        collections = ProductCollection.objects.filter(
            user=request.user
        ).order_by('-is_default', '-created_at')
        
        # 4. Eğer product_id geldiyse, bu ürünün HANGİ listelerde olduğunu bul
        in_collections = set()
        if variant_id and variant_id not in ['undefined', 'null', '']:
            in_collections = set(
                ProductCollectionItem.objects.filter(
                    collection__user=request.user,
                    variant_id=variant_id,
                ).values_list("collection_id", flat=True)
            )



        # 5. Veriyi JSON formatına hazırla
        data = []
        for col in collections:
            data.append({
                'id': col.id,
                'name': col.name,
                'is_default': col.is_default,
                'is_in_list': col.id in in_collections # True ise UI'da kalbi veya tiki dolu göstereceğiz
            })

        return JsonResponse({'success': True, 'collections': data})


class CollectionToggleAPIView(View):
    """
    Bir ürünü belirli bir listeye ekler veya çıkarır. (Bas-Çek)
    """
    def post(self, request, *args, **kwargs):
        # 1. GÜVENLİK
        if not request.user.is_authenticated:
            return JsonResponse({'success': False, 'error': 'Giriş yapmanız gerekiyor.'}, status=403)
        
        try:
            data = json.loads(request.body)
            variant_id = data.get('variant_id')
            collection_id = data.get('collection_id')
            
            variant = get_object_or_404(ProductVariant, id=variant_id)
            collection = get_object_or_404(ProductCollection, id=collection_id, user=request.user)
            
            # get_or_create ile ürünü listeye eklemeye çalış
            offer_id = data.get('offer_id')
            item, created = ProductCollectionItem.objects.get_or_create(
                collection=collection,
                variant=variant,
                defaults={'offer_id': offer_id} # Eğer ilk kez yaratılıyorsa offer_id'yi ekle
            )
            
            # Eğer zaten listedeyse (created=False), listeden çıkar!
            if not created:
                item.delete()
                status = "removed"
            else:
                status = "added"
                
            return JsonResponse({'success': True, 'status': status})
            
        except Exception as e:
            return JsonResponse({'success': False, 'error': str(e)}, status=400)


class CollectionCreateAPIView(View):
    """
    Yeni bir liste yaratır ve ürünü hemen o listeye ekler.
    """
    def post(self, request, *args, **kwargs):
        # 1. GÜVENLİK
        if not request.user.is_authenticated:
            return JsonResponse({'success': False, 'error': 'Giriş yapmanız gerekiyor.'}, status=403)
        
        try:
            data = json.loads(request.body)
            name = data.get('name', '').strip()
            variant_id = data.get('variant_id')
            
            if not name:
                return JsonResponse({'success': False, 'error': 'Liste adı boş olamaz.'}, status=400)
            
            # Aynı isimde liste var mı kontrolü
            if ProductCollection.objects.filter(user=request.user, name__iexact=name).exists():
                return JsonResponse({'success': False, 'error': 'Bu isimde bir listeniz zaten var.'}, status=400)
            
            # Transaction (İşlem bütünlüğü) - Ya liste ve ürün aynı anda eklenir ya da hiçbiri eklenmez
            with transaction.atomic():
                collection = ProductCollection.objects.create(
                    user=request.user,
                    name=name,
                    is_default=False
                )
                
                if variant_id:
                    variant = get_object_or_404(ProductVariant, id=variant_id)
                    offer_id = data.get('offer_id')
                    ProductCollectionItem.objects.create(
                        collection=collection,
                        variant=variant,
                        offer_id=offer_id
                    )
            
            return JsonResponse({
                'success': True, 
                'collection': {
                    'id': collection.id,
                    'name': collection.name,
                    'is_default': collection.is_default,
                    'is_in_list': True
                }
            })
            
        except Exception as e:
            return JsonResponse({'success': False, 'error': str(e)}, status=400)


class ProductQAAskAPIView(View):
    """
    Kullanıcının ürün hakkında soru sormasını sağlar.

    POST
    """

    def post(self, request, *args, **kwargs):

        # -------------------------------------------------
        # AUTH
        # -------------------------------------------------

        if not request.user.is_authenticated:
            return JsonResponse(
                {
                    "success": False,
                    "error": "Giriş yapmanız gerekiyor.",
                },
                status=401,
            )

        # -------------------------------------------------
        # JSON
        # -------------------------------------------------

        try:
            data = json.loads(request.body)
        except json.JSONDecodeError:
            return JsonResponse(
                {
                    "success": False,
                    "error": "Geçersiz JSON verisi.",
                },
                status=400,
            )

        if not isinstance(data, dict):
            return JsonResponse(
                {
                    "success": False,
                    "error": "Geçersiz istek verisi.",
                },
                status=400,
            )

        # -------------------------------------------------
        # INPUT
        # -------------------------------------------------

        product_id = data.get("product_id")
        offer_id = data.get("offer_id")
        variant_id = data.get("variant_id")
        topic = data.get("topic")
        text = data.get("text", "")
        is_anonymous = data.get(
            "is_anonymous",
            False,
        )

        if not product_id:
            return JsonResponse(
                {
                    "success": False,
                    "error": "Ürün ID eksik.",
                },
                status=400,
            )

        # -------------------------------------------------
        # PRODUCT
        # -------------------------------------------------

        product = get_object_or_404(
            Product,
            pk=product_id,
        )

        # -------------------------------------------------
        # CREATE QUESTION
        # -------------------------------------------------

        try:
            question = ProductQAService.create_question(
                product=product,
                user=request.user,
                topic=topic,
                text=text,
                is_anonymous=is_anonymous,
                offer_id=offer_id,
                variant_id=variant_id,
            )

        except ValueError as exc:
            return JsonResponse(
                {
                    "success": False,
                    "error": str(exc),
                },
                status=400,
            )

        return JsonResponse(
            {
                "success": True,
                "question_id": question.id,
                "message": "Sorunuz başarıyla gönderildi.",
            },
            status=201,
        )

class ProductQAListAPIView(View):
    """
    Ürün detay sayfasındaki filtrelemelerde sayfa yenilenmemesi için
    sadece QA HTML listesini döndüren AJAX Endpointi.
    GET: /api/qa/list/<product_id>/
    """
    def get(self, request, product_id, *args, **kwargs):
        product = get_object_or_404(Product, pk=product_id, status=ProductStatus.ACTIVE)
        
        # ProductQAService senin filtreni, aramanı ve sayfalamanı zaten yapıyor!
        qa_context = ProductQAService.get_context(
            product=product,
            request=request,
            offer_id=request.GET.get("offer"),
        )
        
        # Hazırladığımız qa_list.html'i backend'de render et
        html = render_to_string(
            "products/public/partials/qa_list.html", 
            qa_context, 
            request=request
        )
        
        return JsonResponse({"success": True, "html": html})


class ProductQAUpvoteAPIView(View):
    """
    Kullanıcının görünür ve cevaplanmış bir soruya
    faydalı oyu vermesini sağlar.

    POST
    """

    def post(self, request, *args, **kwargs):

        # -------------------------------------------------
        # AUTH
        # -------------------------------------------------

        if not request.user.is_authenticated:
            return JsonResponse(
                {
                    "success": False,
                    "error": "Giriş yapmanız gerekiyor.",
                },
                status=401,
            )

        # -------------------------------------------------
        # JSON
        # -------------------------------------------------

        try:
            data = json.loads(request.body)
        except json.JSONDecodeError:
            return JsonResponse(
                {
                    "success": False,
                    "error": "Geçersiz JSON verisi.",
                },
                status=400,
            )

        if not isinstance(data, dict):
            return JsonResponse(
                {
                    "success": False,
                    "error": "Geçersiz istek verisi.",
                },
                status=400,
            )

        # -------------------------------------------------
        # QUESTION ID
        # -------------------------------------------------

        question_id = data.get(
            "question_id",
        )

        if not question_id:
            return JsonResponse(
                {
                    "success": False,
                    "error": "Soru ID eksik.",
                },
                status=400,
            )

        try:
            question_id = int(question_id)
        except (TypeError, ValueError):
            return JsonResponse(
                {
                    "success": False,
                    "error": "Geçersiz soru ID.",
                },
                status=400,
            )

        # -------------------------------------------------
        # QUESTION
        # -------------------------------------------------

        question = get_object_or_404(
            ProductQuestion,
            pk=question_id,
        )

        # -------------------------------------------------
        # UPVOTE
        # -------------------------------------------------

        try:
            upvotes = ProductQAService.upvote_question(
                question=question,
                user=request.user,
            )

        except PermissionError as exc:
            return JsonResponse(
                {
                    "success": False,
                    "error": str(exc),
                },
                status=403,
            )

        except ValueError as exc:
            return JsonResponse(
                {
                    "success": False,
                    "error": str(exc),
                },
                status=400,
            )

        return JsonResponse(
            {
                "success": True,
                "upvoted": True,
                "upvotes": upvotes,
                "message": "Oy vermeniz kaydedildi.",
            },
            status=200,
        )




class ProductQAAnswerDeleteAPIView(View):
    """
    Kullanıcının kendi verdiği cevabı silmesini sağlar.

    DELETE
    """

    def delete(
        self,
        request,
        answer_id,
        *args,
        **kwargs,
    ):
        # -------------------------------------------------
        # AUTH
        # -------------------------------------------------

        if not request.user.is_authenticated:
            return JsonResponse(
                {
                    "success": False,
                    "error": "Giriş yapmanız gerekiyor.",
                },
                status=401,
            )

        # -------------------------------------------------
        # ANSWER
        # -------------------------------------------------

        answer = get_object_or_404(
            ProductAnswer,
            pk=answer_id,
        )

        # -------------------------------------------------
        # DELETE
        # -------------------------------------------------

        try:
            ProductQAService.delete_answer(
                answer=answer,
                user=request.user,
            )

        except PermissionError as exc:
            return JsonResponse(
                {
                    "success": False,
                    "error": str(exc),
                },
                status=403,
            )

        return JsonResponse(
            {
                "success": True,
                "message": "Cevabınız silindi.",
            },
            status=200,
        )