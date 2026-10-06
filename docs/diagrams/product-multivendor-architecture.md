# Product / Multi-Vendor Architecture Diyagramları

## 1. Global Catalog ve Seller Offer

```mermaid
graph TD
    CATEGORY[Category]
    BRAND[Brand]
    PRODUCT[Product]
    VARIANT[ProductVariant]
    STORE[Store]
    OFFER[StoreProduct]
    HISTORY[ProductPriceHistory]

    CATEGORY --> PRODUCT
    BRAND --> PRODUCT
    PRODUCT --> VARIANT
    STORE --> OFFER
    VARIANT --> OFFER
    OFFER --> HISTORY

    classDef catalog fill:#eef6ff,stroke:#2563eb,stroke-width:1px;
    classDef seller fill:#f4f4f5,stroke:#52525b,stroke-width:1px;
    class PRODUCT,VARIANT,CATEGORY,BRAND catalog;
    class STORE,OFFER,HISTORY seller;
```

> GitHub'daki normal Markdown görünümünde Mermaid kodu kutular ve bağlantılar şeklinde render edilir.

---

## 2. Variant / Attribute EAV

```mermaid
erDiagram
    CATEGORY ||--o{ CATEGORY_ATTRIBUTE : "tanımlar"
    ATTRIBUTE ||--o{ CATEGORY_ATTRIBUTE : "kategorilerde kullanılır"
    ATTRIBUTE ||--o{ ATTRIBUTE_VALUE : "değerleri içerir"
    PRODUCT ||--o{ PRODUCT_VARIANT : "varyantları içerir"
    PRODUCT_VARIANT }o--o{ ATTRIBUTE_VALUE : "özellik değerleri"

    CATEGORY_ATTRIBUTE {
        bigint id PK
        bigint category_id FK
        bigint attribute_id FK
        int sort_order
        boolean is_filterable
        boolean is_required
        boolean is_variant
        boolean is_visual
        boolean allow_custom_values
    }

    ATTRIBUTE {
        bigint id PK
        string name UK
        boolean is_active
    }

    ATTRIBUTE_VALUE {
        bigint id PK
        bigint attribute_id FK
        string value
        boolean is_active
    }

    PRODUCT {
        bigint id PK
        bigint category_id FK
        bigint brand_id FK
        bigint default_variant_id FK
        string name
        string slug UK
        string status
    }

    PRODUCT_VARIANT {
        bigint id PK
        bigint product_id FK
        string barcode UK
        boolean is_active
    }
```

---

## 3. Ürün Görselleri

```mermaid
erDiagram
    PRODUCT ||--o{ PRODUCT_IMAGE_GROUP : "görsel grupları"
    PRODUCT_IMAGE_GROUP ||--o{ PRODUCT_IMAGE : "görselleri içerir"
    PRODUCT_IMAGE_GROUP }o--o{ ATTRIBUTE_VALUE : "görsel özellikleri"

    PRODUCT_IMAGE_GROUP {
        bigint id PK
        bigint product_id FK
        int sort_order
        boolean is_active
        datetime created_at
        datetime updated_at
    }

    PRODUCT_IMAGE {
        bigint id PK
        bigint group_id FK
        string image
        string alt_text
        boolean is_main
        int sort_order
    }
```

---

## 4. Seller Offer ve Buy Box

```mermaid
flowchart LR
    P[Product]
    V[ProductVariant]
    S1[Store A]
    S2[Store B]
    S3[Store C]
    O1[StoreProduct]
    O2[StoreProduct]
    O3[StoreProduct]
    F[Purchasable offers]
    B[Buy Box]

    P --> V
    S1 --> O1
    S2 --> O2
    S3 --> O3
    V --> O1
    V --> O2
    V --> O3
    O1 --> F
    O2 --> F
    O3 --> F
    F --> B
```

---

## 5. Product Draft → Catalog

```mermaid
flowchart TD
    D[ProductDraft]
    M[DuplicateProductService]
    MATCH{Katalog eşleşmesi?}
    E[Mevcut Product]
    N[Yeni Product]
    DV[ProductDraftVariant]
    PV[ProductVariant]
    SP[StoreProduct]
    IMG["ProductImageGroup + ProductImage"]
    PUB["ProductDraft = PUBLISHED"]

    D --> M
    M --> MATCH
    MATCH -->|Evet| E
    MATCH -->|Hayır| N

    D --> DV
    N --> PV
    E --> PV
    DV --> PV
    PV --> SP
    D --> IMG
    IMG --> PV
    N --> SP
    E --> SP
    SP --> PUB
```

---

## 6. Product Wizard

```mermaid
flowchart TD
    S[Seller]
    STEP1["Step 1<br/>Temel bilgiler"]
    MATCH[Duplicate / Match]
    STEP2["Step 2<br/>Varyantlar"]
    STEP3["Step 3<br/>Görseller"]
    STEP4["Step 4<br/>Fiyat / Stok / SKU / Barkod"]
    STEP5["Step 5<br/>İnceleme"]
    DECISION{Existing Product?}
    EXISTING[Mevcut Product'a katkı]
    NEW[Yeni Product oluştur]
    OFFER[StoreProduct]

    S --> STEP1
    STEP1 --> MATCH
    MATCH --> STEP2
    STEP2 --> STEP3
    STEP3 --> STEP4
    STEP4 --> STEP5
    STEP5 --> DECISION
    DECISION -->|Evet| EXISTING
    DECISION -->|Hayır| NEW
    EXISTING --> OFFER
    NEW --> OFFER
```

