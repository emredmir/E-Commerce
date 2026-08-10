from products.models import (
    Product,
    ProductStatus,
)


class DuplicateProductService:
    """
    ProductDraft için katalog eşleşmesi bulur.

    Öncelik:

    1. Aynı kategori
    2. Aynı normalized_key
    3. Aynı marka
    4. İsim benzerliği

    """
    TOKEN_MATCH_THRESHOLD = 0.75

    @staticmethod
    def find_match(*, draft):

        products = (
            Product.objects
            .filter(
                category=draft.category,
                status=ProductStatus.ACTIVE,
            )
            .select_related(
                "brand",
                "category",
            )
        )


        
        # 1) Exact normalized key
        
        exact_matches = (
            products
            .filter(
                normalized_key=draft.normalized_key,
            )
        )


        
        # Marka varsa önce aynı marka
        
        if draft.brand:

            product = (
                exact_matches
                .filter(
                    brand=draft.brand,
                )
                .first()
            )

            if product:
                return product


        product = exact_matches.first()

        if product:
            return product


        
        # 2) Token benzerliği
        
        best_product = None

        best_score = 0


        for product in products:


            score = (
                DuplicateProductService
                .calculate_token_similarity(
                    draft.tokens,
                    product.tokens,
                )
            )


            
            # Marka aynıysa bonus
            
            if (
                draft.brand
                and product.brand_id == draft.brand_id
            ):
                score = min(score + 0.10, 1.0)



            if score > best_score:

                best_score = score
                best_product = product



        if best_score >= DuplicateProductService.TOKEN_MATCH_THRESHOLD:
            return product

        return None



    @staticmethod
    def calculate_token_similarity(
        draft_tokens,
        product_tokens,
    ):
        """
        İki ürün adının token benzerliği.

        Örnek:

        draft:
        [
            apple,
            iphone,
            15,
            128gb
        ]

        product:
        [
            apple,
            iphone,
            15
        ]

        sonuç:
        0.75
        """


        if not draft_tokens or not product_tokens:
            return 0


        draft_set = set(
            draft_tokens
        )

        product_set = set(
            product_tokens
        )


        intersection = (
            draft_set &
            product_set
        )


        union = (
            draft_set |
            product_set
        )


        if not union:
            return 0


        return (
            len(intersection)
            /
            len(union)
        )