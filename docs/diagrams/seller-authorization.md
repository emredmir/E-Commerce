# Seller Authorization / Ownership

```mermaid
flowchart TD
    REQUEST["HTTP Request"] --> AUTH["SellerRequiredMixin"]

    AUTH -->|Giriş yok| LOGIN["Login / redirect"]
    AUTH -->|Seller uygun değil| SELLER_PAGE["Seller application / redirect"]
    AUTH -->|Seller uygun| OWNER["StoreOwnerMixin"]

    OWNER --> LOOKUP["Store lookup"]
    LOOKUP --> CHECK{"seller == request.user.seller_profile<br/>and status == APPROVED?"}

    CHECK -->|Hayır| DENIED["Erişim reddedilir"]
    CHECK -->|Evet| STORE["Authorized Store"]

    STORE --> VIEW["Seller View"]
    VIEW --> SERVICE["Domain Service"]
    SERVICE --> DOMAIN_CHECK["Store / SubOrder ownership validation"]
    DOMAIN_CHECK --> DB["Database Operation"]
```

## Seller erişim sınırı

```text
User
  │
  ├── SellerProfile
  │
  └── Approved Store
           │
           └── Seller operations
                  ├── Inventory
                  ├── Offers
                  ├── Product Wizard
                  ├── Questions
                  └── Orders
```

Ana prensip:

> URL'de bir `store_slug` veya `suborder_number` bulunması tek başına yetki sağlamaz; kaydın giriş yapan seller'a ait olması ayrıca doğrulanır.
