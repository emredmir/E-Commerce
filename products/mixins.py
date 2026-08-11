from django.contrib import messages
from django.contrib.auth.mixins import AccessMixin
from django.shortcuts import redirect, get_object_or_404


from store.models import Store
from products.models import ProductDraft


class SellerRequiredMixin(AccessMixin):
    """
    Sadece onaylı satıcıların erişimine izin verir.
    """

    def dispatch(self, request, *args, **kwargs):
        if not request.user.is_authenticated:
            messages.info(
                request,
                "Bu sayfaya erişmek için giriş yapmalısınız.",
            )
            return self.handle_no_permission()

        if not request.user.is_seller:
            messages.warning(
                request,
                "Bu sayfaya erişmek için onaylı satıcı hesabına sahip olmalısınız.",
            )
            return redirect("accounts:seller_form")

        return super().dispatch(request, *args, **kwargs)


class StoreOwnerMixin:
    """
    URL'deki store_slug'ın giriş yapan kullanıcıya ait
    onaylı mağaza olduğunu doğrular.

    Aynı request içerisinde mağaza yalnızca bir kez sorgulanır.
    """

    store_lookup_field = "store_slug"

    def get_store(self):
        if not hasattr(self, "_store"):
            self._store = (
                Store.objects.select_related("seller")
                .filter(
                    slug=self.kwargs[self.store_lookup_field],
                    seller=self.request.user.seller_profile,
                    status="approved",
                )
                .first()
            )

        return self._store

    def dispatch(self, request, *args, **kwargs):
        if self.get_store() is None:
            messages.error(
                request,
                "Mağaza bulunamadı veya bu mağazaya erişim yetkiniz yok.",
            )

            return redirect("store:store_list")

        return super().dispatch(request, *args, **kwargs)


class DraftWizardMixin:
    """
    Wizard adımlarında ortak kullanılan
    ProductDraft işlemleri.
    """
    def get_draft(
        self,
        *,
        draft_id,
    ):
        """
        Giriş yapan satıcıya ait aktif taslağı döndürür.
        """
        return get_object_or_404(
            ProductDraft.objects.select_related(
                "category",
                "brand",
                "seller",
                "store",
            ),
            pk=draft_id,
            seller=self.request.user,
            store=self.get_store(),
            status=ProductDraft.Status.DRAFT,
        )
