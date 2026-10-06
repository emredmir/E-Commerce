# Seller Architecture Diagram

```mermaid
flowchart TD
    USER["CustomUser"] --> PROFILE["SellerProfile"]

    PROFILE --> APPROVAL["Platform Seller Approval"]
    APPROVAL --> FLAG["CustomUser.is_seller"]

    PROFILE --> STORES["Store"]
    STORES --> CATALOG["Product / ProductVariant"]
    CATALOG --> OFFER["StoreProduct"]

    OFFER --> INVENTORY["Seller Inventory"]
    OFFER --> CUSTOMER_ORDER["Customer Order"]

    CUSTOMER_ORDER --> SUBORDER["SubOrder"]
    STORES --> SUBORDER

    SUBORDER --> SHIPPING["ShippingService"]
    SUBORDER --> CANCELLATION["CancellationService"]
    CANCELLATION --> REFUND["RefundService"]

    APPROVAL --> IYZICO["IyzicoSubmerchantService"]

    ADMIN["Django Admin"] --> APPROVAL
    ADMIN --> STORES
    ADMIN --> UPDATES["SellerProfileUpdateRequest / StoreUpdateRequest"]

    PROFILE --> UPDATES

    subgraph AUTH["Yetkilendirme"]
        SELLER_REQUIRED["SellerRequiredMixin"]
        STORE_OWNER["StoreOwnerMixin"]
        DOMAIN_AUTH["Service seviyesinde ownership kontrolü"]
    end

    SELLER_REQUIRED --> STORE_OWNER
    STORE_OWNER --> DOMAIN_AUTH
```

## Katmanların anlamı

- `SellerProfile`: satıcı kimliği ve platforma ait seller bilgileri
- `Store`: seller'ın operasyon yaptığı mağaza
- `StoreProduct`: mağazanın katalog ürünü için satış teklifi
- `SubOrder`: seller açısından yönetilen sipariş birimi
- Admin: platform operasyonlarının yürütüldüğü yönetim alanı
