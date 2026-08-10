from django.http import JsonResponse
from django.views import View

from products.models import Category, Brand



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